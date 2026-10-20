import queue
import threading
import unittest
from unittest.mock import patch

import app
from quota import QuotaError


RESULT = {"rateLimitsByLimitId":{"codex":{
    "primary":{"usedPercent":52,"windowDurationMins":300,"resetsAt":1999999999},
    "secondary":{"usedPercent":34,"windowDurationMins":10080,"resetsAt":1999999999},
}}}


class WorkerTests(unittest.TestCase):
    def test_automatic_refresh_and_cleanup(self):
        created=[]
        methods=[]

        class Client:
            def __init__(self,*args,**kwargs):
                self.closed=False
                created.append(self)
            def initialize(self):
                pass
            def request(self,method):
                methods.append(method)
                return RESULT
            def close(self):
                self.closed=True

        events=queue.Queue()
        with patch.object(app,"RpcClient",Client), patch.object(app,"discover_codex",return_value="codex.exe"), patch.object(app,"REFRESH_SECONDS",0.02):
            worker=app.QuotaWorker(events)
            try:
                worker.start()
                snapshots=[]
                while len(snapshots)<2:
                    event,value=events.get(timeout=2)
                    if event=="snapshot":
                        snapshots.append(value)
                self.assertEqual(snapshots[-1].short.remaining,48)
                self.assertEqual(snapshots[-1].week.remaining,66)
            finally:
                worker.close()
        self.assertFalse(worker.thread.is_alive())
        self.assertTrue(all(client.closed for client in created))
        self.assertTrue(all(method=="account/rateLimits/read" for method in methods))

    def test_shutdown_cancels_in_flight_query(self):
        entered=threading.Event()
        cancelled=threading.Event()

        class Client:
            def __init__(self,*args,**kwargs):
                pass
            def initialize(self):
                pass
            def request(self,method):
                entered.set()
                cancelled.wait(timeout=3)
                raise QuotaError("closed")
            def close(self):
                cancelled.set()

        with patch.object(app,"RpcClient",Client),patch.object(app,"discover_codex",return_value="codex.exe"):
            worker=app.QuotaWorker(queue.Queue())
            worker.start()
            self.assertTrue(entered.wait(timeout=2))
            worker.close()
            self.assertTrue(cancelled.is_set())
            self.assertFalse(worker.thread.is_alive())


if __name__=="__main__":
    unittest.main()
