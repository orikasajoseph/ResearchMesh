"""测试专用临时 HTTP 服务：随机端口、仅回环地址、退出时关闭。"""

import contextlib
import socket
import threading
import time

import uvicorn


@contextlib.contextmanager
def local_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(
        "api:app", host="127.0.0.1", port=port, log_level="error", access_log=False
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("测试 HTTP 服务未能启动")
            time.sleep(0.01)
        yield "http://127.0.0.1:" + str(port)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        if thread.is_alive():
            raise RuntimeError("测试 HTTP 服务未能关闭")
