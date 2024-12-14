import asyncio
import io
from typing import Optional, Dict, Any
from datetime import datetime
from collections import deque

from rich.text import Text
from loguru import logger
from aiocache import Cache

from .link import Link
from .tele import ClientsSession

logger = logger.bind(scheme="telenotifier")

# 增加写入缓冲区大小以提高性能
from asyncio import constants
constants.LOG_THRESHOLD_FOR_CONNLOST_WRITES = 1000000

class TelegramStreamManager:
    """Telegram消息流管理器，用于管理和复用消息流实例"""
    _instances: Dict[str, 'TelegramStream'] = {}
    _cache = Cache()
    
    @classmethod
    async def get_stream(cls, account: Dict[str, Any], proxy=None, basedir=None, instant=False) -> 'TelegramStream':
        key = f"{account['phone']}_{instant}"
        stream = cls._instances.get(key)
        if stream is None:
            stream = TelegramStream(account, proxy, basedir, instant)
            cls._instances[key] = stream
        return stream
    
    @classmethod
    async def close_all(cls):
        """关闭所有消息流"""
        for stream in cls._instances.values():
            await stream.close()
        cls._instances.clear()

class TelegramStream(io.TextIOWrapper):
    """消息推送处理器类"""

    def __init__(self, account, proxy=None, basedir=None, instant=False):
        super().__init__(io.BytesIO(), line_buffering=True)
        self.account = account
        self.proxy = proxy
        self.basedir = basedir
        self.instant = instant
        
        # 消息队列和批处理设置
        self.queue = asyncio.Queue()
        self.batch_size = 10  # 批处理大小
        self.batch_timeout = 2.0  # 批处理超时时间(秒)
        self.message_buffer = deque(maxlen=100)  # 消息缓冲区，限制大小
        
        # 重试设置
        self.max_retries = 3
        self.retry_delay = 1.0
        
        # 限流设置
        self.rate_limit = 30  # 每分钟最大消息数
        self.last_send_time = datetime.now()
        self.sent_count = 0
        
        # 启动看门狗和批处理任务
        self.watch = asyncio.create_task(self.watchdog())
        self.batch_task = asyncio.create_task(self.batch_processor())
        
        # 客户端会话缓存
        self._client_session: Optional[ClientsSession] = None
        self._link: Optional[Link] = None

    async def get_client_session(self) -> Optional[ClientsSession]:
        """获取或创建客户端会话"""
        if self._client_session is None:
            self._client_session = ClientsSession(
                [self.account], 
                proxy=self.proxy, 
                basedir=self.basedir
            )
            await self._client_session.__aenter__()
        return self._client_session

    async def get_link(self) -> Optional[Link]:
        """获取或创建Link实例"""
        if self._link is None:
            session = await self.get_client_session()
            async for tg in session:
                self._link = Link(tg)
                break
        return self._link

    async def watchdog(self):
        """消息处理看门狗"""
        while True:
            try:
                message = await self.queue.get()
                self.message_buffer.append(message)
                
                # 检查是否需要限流
                now = datetime.now()
                if (now - self.last_send_time).total_seconds() >= 60:
                    self.sent_count = 0
                    self.last_send_time = now
                
                if self.sent_count >= self.rate_limit:
                    await asyncio.sleep(
                        60 - (now - self.last_send_time).total_seconds()
                    )
                    continue
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"消息处理错误: {e}")

    async def batch_processor(self):
        """批量处理消息"""
        while True:
            try:
                messages = []
                try:
                    # 等待积累足够的消息或超时
                    while len(messages) < self.batch_size:
                        message = await asyncio.wait_for(
                            self.queue.get(), 
                            timeout=self.batch_timeout
                        )
                        messages.append(message)
                except asyncio.TimeoutError:
                    pass
                
                if messages:
                    # 批量发送消息
                    combined_message = "\n".join(messages)
                    for retry in range(self.max_retries):
                        try:
                            result = await asyncio.wait_for(
                                self.send(combined_message), 
                                timeout=10
                            )
                            if result:
                                self.sent_count += 1
                                break
                            await asyncio.sleep(self.retry_delay)
                        except asyncio.TimeoutError:
                            if retry == self.max_retries - 1:
                                logger.warning("推送消息到 Telegram 超时.")
                        except Exception as e:
                            if retry == self.max_retries - 1:
                                logger.error(f"发送消息错误: {e}")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"批处理错误: {e}")
                await asyncio.sleep(1)

    async def send(self, message: str) -> bool:
        """发送消息"""
        try:
            link = await self.get_link()
            if link:
                if self.instant:
                    return await link.send_msg(message)
                else:
                    return await link.send_log(message)
            return False
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            return False

    def write(self, message: str):
        """写入消息到队列"""
        try:
            message = Text.from_markup(message).plain
            if message.endswith("\n"):
                message = message[:-1]
            if message:
                self.queue.put_nowait(message)
        except Exception as e:
            logger.error(f"写入消息错误: {e}")

    async def close(self):
        """关闭流和清理资源"""
        try:
            # 取消任务
            if hasattr(self, 'watch'):
                self.watch.cancel()
            if hasattr(self, 'batch_task'):
                self.batch_task.cancel()
            
            # 等待队列处理完成
            while not self.queue.empty():
                await asyncio.sleep(0.1)
            
            # 关闭会话
            if self._client_session:
                await self._client_session.__aexit__(None, None, None)
                self._client_session = None
            self._link = None
            
        except Exception as e:
            logger.error(f"关闭流错误: {e}")
        finally:
            await super().close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
