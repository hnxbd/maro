# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import os
import threading
import time

import redis

from maro.cli.local.meta import JobStatus, RedisHashKey
from maro.cli.local.utils import (
    build_image, image_exists, poll, start_rl_job_in_background, start_rl_job_in_containers, term
)

if __name__ == "__main__":
    redis_port = int(os.getenv("REDISPORT", default=19999))
    redis_conn = redis.Redis(host="localhost", port=redis_port)
    started, max_running = {}, int(os.getenv("MAXRUNNING", default=1))
    query_every = int(os.getenv("QUERYEVERY", default=5))
    sigterm_timeout = int(os.getenv("SIGTERMTIMEOUT", default=3))
    containerized = os.getenv("CONTAINERIZE", default="False") == "True"

    # thread to monitor a job
    def monitor(job_name):
        removed, error, err_out, running = False, False, None, started[job_name]
        while running:
            error, err_out, running = poll(running)
            # check if the job has been marked as REMOVED before termination
            details = json.loads(redis_conn.hget(RedisHashKey.JOB_DETAILS, job_name))
            if details["status"] == JobStatus.REMOVED:
                removed = True
                break

            if error:
                break

        if removed:
            term(started[job_name], job_name, timeout=sigterm_timeout)
            redis_conn.hdel(RedisHashKey.JOB_DETAILS, job_name)
            redis_conn.hdel(RedisHashKey.JOB_CONF, job_name)
            return

        if error:
            term(started[job_name], job_name, timeout=sigterm_timeout)
            details["status"] = JobStatus.ERROR
            details["error_message"] = err_out
            redis_conn.hset(RedisHashKey.JOB_DETAILS, job_name, json.dumps(details))
        else:  # all job processes terminated normally
            details["status"] = JobStatus.FINISHED
            redis_conn.hset(RedisHashKey.JOB_DETAILS, job_name, json.dumps(details))

        # Continue to monitor if the job is marked as REMOVED
        while json.loads(redis_conn.hget(RedisHashKey.JOB_DETAILS, job_name))["status"] != JobStatus.REMOVED:
            time.sleep(query_every)

        term(started[job_name], job_name, timeout=sigterm_timeout)
        redis_conn.hdel(RedisHashKey.JOB_DETAILS, job_name)
        redis_conn.hdel(RedisHashKey.JOB_CONF, job_name)

    while True:
        # check for pending jobs
        job_details = redis_conn.hgetall(RedisHashKey.JOB_DETAILS)
        if job_details:
            num_running, pending = 0, []
            for job_name, details in job_details.items():
                job_name, details = job_name.decode(), json.loads(details)
                if details["status"] == JobStatus.RUNNING:
                    num_running += 1
                elif details["status"] == JobStatus.PENDING:
                    pending.append((job_name, json.loads(redis_conn.hget(RedisHashKey.JOB_CONF, job_name))))

            for job_name, conf in pending[:max(0, max_running - num_running)]:
                if containerized and not image_exists():
                    redis_conn.hset(
                        RedisHashKey.JOB_DETAILS, job_name, json.dumps({"status": JobStatus.IMAGE_BUILDING})
                    )
                    build_image()

                if containerized:
                    started[job_name] = start_rl_job_in_containers(conf)
                    details["containers"] = started[job_name]
                else:
                    started[job_name] = start_rl_job_in_background(conf, redis_port)
                    details["pids"] = [proc.pid for proc in started[job_name]]
                details = {"status": JobStatus.RUNNING, "start_time": time.time()}
                redis_conn.hset(RedisHashKey.JOB_DETAILS, job_name, json.dumps(details))
                threading.Thread(target=monitor, args=(job_name,)).start()  # start job monitoring thread

        time.sleep(query_every)
