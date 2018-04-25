import json
import multiprocessing as mp

from requests import get, post

from .api import API
from .conf import Config
from .docker_cli import DockerThread
from .listener import Listener
from .logger import Log


class WorkerThread:
    def __init__(self, thread_no: int = 0):
        Log.info(
            "Initiating WorkerThread {thread_no}".format(thread_no=thread_no))
        self.id = thread_no
        self.docker = DockerThread()
        self.api_d = API()
        file_name = "worker_conf-%s" % thread_no
        data = Config.load(file_name)
        if data is None:
            data = self.api_d.create_worker()
            Config.save(file_name, data, file_type="json")
        self.worker_id = data["worker_id"]
        self.api_d.worker_id = self.worker_id
        path = Config.save("gcreds", data["subscriber_json"], file_type="json")
        Config.put("GOOGLE_APPLICATION_CREDENTIALS", path)
        self.listener = Listener(
            data["subscription_name"], data["subscription_string"], self.run)
        self.job_id = None

    def run(self, message):
        """
        Run this function when
        :param message:
        :return:
        """
        job_id = message['data']
        Log.info("Starting worker, Job ID: %s" % job_id)
        self.job_id = job_id
        job_data = self.api_d.initiate_job(job_id)
        docker_data = job_data["image_dict"]
        self.docker.set_image_info(docker_data)
        self.docker.run()
        ip_address = self.docker.get_ip()
        url = "http://{ip_address}:8000/".format(ip_address=ip_address)
        request_data = job_data.setdefault("data", None)
        if request_data:
            Log.info("Worker responded with Success.")
            response = post(url, json=request_data)
        else:
            Log.info("Worker Failed")
            response = get(url)
        self.api_d.complete_job(
            job_id, data=response.content, fail=response.ok)
        message.ack()

    def start(self):
        try:
            Log.info("Starting worker loop")
            while True:
                try:
                    future = self.listener.listen()
                    self.api_d.register_worker()
                    future.result()
                except KeyboardInterrupt:
                    Log.info("Worker killed by user")
                    if self.job_id is not None:
                        self.api_d.complete_job(job_id=self.job_id, fail=True)
                    exit(0)
                except Exception as e:
                    if self.job_id is not None:
                        self.api_d.complete_job(job_id=self.job_id, fail=True)
                    Log.info("Worker Error: %s" % str(e))
                    exit(127)
        except KeyboardInterrupt:
            exit(0)


class WorkerPool:
    def __init__(self):
        self.thread_count = int(Config.get("thread_count", 1))
        self.threads = []
        for each in range(0, self.thread_count):
            w_thread = WorkerThread(each)
            self.threads.append(mp.Process(target=w_thread.start))

    def start(self):
        for thread in self.threads:
            thread.start()

    def kill(self):
        for thread in self.threads:
            thread.terminate()
