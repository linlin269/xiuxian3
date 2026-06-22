"""同 client_id 新连接接管旧连接示例。

启动服务后，可以用两个终端分别运行两个连接。
第二个连接建立后，第一个连接会被服务端关闭。
"""

import asyncio
import websockets


async def main() -> None:
    client_id = "same"
    url = f"ws://127.0.0.1:7001/ws/bot/{client_id}"

    async with websockets.connect(url) as websocket:
        print(f"已连接: {url}")
        print("保持当前窗口不动，再运行一次本示例，当前连接会被关闭。")

        try:
            async for message in websocket:
                print(f"收到消息: {message}")
        finally:
            print("连接已关闭，通常表示同 client_id 的新连接已经接管。")


if __name__ == "__main__":
    asyncio.run(main())
