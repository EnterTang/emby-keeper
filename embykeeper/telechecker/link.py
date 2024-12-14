import asyncio
import random
import time
from typing import Callable, Coroutine, List, Optional, Tuple, Union
import uuid

import tomli
from loguru import logger
from telethon import events, types
from telethon.errors import UserBlockedError, FloodWaitError

from ..utils import async_partial, truncate_str
from .lock import super_ad_shown, super_ad_shown_lock, authed_services, authed_services_lock
from .tele import Client


class LinkError(Exception):
    pass


class Link:
    """云服务类, 用于认证和高级权限任务通讯."""

    bot = "embykeeper_auth_bot"

    def __init__(self, client: Client):
        self.client = client
        self.log = logger.bind(scheme="telelink", username=client.me.name)

    @property
    def instance(self):
        """当前设备识别码."""
        rd = random.Random()
        rd.seed(uuid.getnode())
        return uuid.UUID(int=rd.getrandbits(128))

    async def delete_messages(self, messages: List[types.Message]):
        """删除一系列消息."""

        async def delete(m: types.Message):
            try:
                await m.delete(revoke=True)
                text = m.text or m.caption or "图片或其他内容"
                text = truncate_str(text.replace("\n", ""), 30)
                self.log.debug(f"[gray50]删除了 API 消息记录: {text}[/]")
            except asyncio.CancelledError:
                pass

        return await asyncio.gather(*[delete(m) for m in messages])

    async def post(
        self,
        cmd,
        photo=None,
        condition: Callable = None,
        timeout: int = 20,
        retries=3,
        name: str = None,
        fail: bool = False,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        向机器人发送请求.
        参数:
            cmd: 命令字符串
            condition: 布尔或函数, 参数为响应 toml 的字典形式, 决定该响应是否为有效响应.
            timeout: 超时 (s)
            retries: 最大重试次数
            name: 请求名称, 用于用户提示
            fail: 当出现错误时抛出错误, 而非发送日志
        """
        # 使用缓存键
        cache_key = f"{cmd}_{self.instance}"
        cached_result = await self.client.cache.get(cache_key)
        if cached_result:
            return cached_result

        for r in range(retries):
            self.log.debug(f"[gray50]禁用提醒 {timeout} 秒: {self.bot}[/]")
            try:
                # 批量处理消息
                messages = []
                future = asyncio.Future()
                
                # 优化事件处理器
                async def message_handler(event):
                    if event.chat_id != self.bot:
                        return
                    try:
                        toml = tomli.loads(event.text)
                        if toml.get("command", None) == cmd:
                            if condition is None or (
                                await condition(toml) if asyncio.iscoroutinefunction(condition) 
                                else condition(toml)
                            ):
                                future.set_result(toml)
                    except (tomli.TOMLDecodeError, Exception) as e:
                        self.log.debug(f"消息处理错误: {e}")
                    finally:
                        await self.delete_messages([event.message])

                handler = self.client._client.add_event_handler(
                    message_handler,
                    events.NewMessage(chats=self.bot)
                )

                try:
                    # 发送消息
                    if photo:
                        messages.append(
                            await self.client.send_file(self.bot, photo, caption=cmd)
                        )
                    else:
                        messages.append(
                            await self.client.send_message(self.bot, cmd)
                        )
                    self.log.debug(f"[gray50]-> {cmd}[/]")

                    # 等待响应
                    results = await asyncio.wait_for(future, timeout=timeout)
                    
                    # 缓存结果
                    if results and results.get("status") == "ok":
                        await self.client.cache.set(cache_key, results, ttl=3600)  # 缓存1小时
                    
                    return results

                except asyncio.TimeoutError:
                    await self.delete_messages(messages)
                    if r + 1 < retries:
                        self.log.info(f"{name}超时 ({r + 1}/{retries}), 将在 3 秒后重试.")
                        await asyncio.sleep(3)
                        continue
                    msg = f"{name}超时 ({r + 1}/{retries})."
                    if fail:
                        raise LinkError(msg)
                    self.log.warning(msg)
                    return None

                except UserBlockedError:
                    msg = "您在账户中禁用了用于 API 信息传递的 Bot: @embykeeper_auth_bot, 这将导致 embykeeper 无法运行, 请尝试取消禁用."
                    if fail:
                        raise LinkError(msg)
                    self.log.error(msg)
                    return None

                finally:
                    await self.delete_messages(messages)
                    self.client._client.remove_event_handler(handler)

            except FloodWaitError as e:
                wait_time = e.seconds
                self.log.info(f"请求频率限制, 等待 {wait_time} 秒后重试.")
                await asyncio.sleep(wait_time)
                continue

            except Exception as e:
                self.log.error(f"请求出错: {e}")
                if fail:
                    raise LinkError(str(e))
                return None

    async def auth(self, service: str, log_func=None):
        """向机器人发送授权请求."""
        # 使用缓存优化
        cache_key = f"auth_{service}_{self.client.me.id}"
        cached_result = await self.client.cache.get(cache_key)
        if cached_result is not None:
            return cached_result

        async with authed_services_lock:
            user_auth_cache = authed_services.get(self.client.me.id, {}).get(service, None)
            if user_auth_cache is not None:
                await self.client.cache.set(cache_key, user_auth_cache, ttl=3600)
                return user_auth_cache

        if not log_func:
            result = await self.post(
                f"/auth {service} {self.instance}", 
                name=f"服务 {service.upper()} 认证"
            )
            if result:
                await self.client.cache.set(cache_key, bool(result), ttl=3600)
            return bool(result)

        try:
            await self.post(
                f"/auth {service} {self.instance}",
                name=f"服务 {service.upper()} 认证",
                fail=True,
            )
        except LinkError as e:
            log_func(f"初始化错误: 使用 {service.upper()} 服务, 但{e}")
            if "权限不足" in str(e):
                await self._show_super_ad()
            async with authed_services_lock:
                authed_services.setdefault(self.client.me.id, {})[service] = False
                await self.client.cache.set(cache_key, False, ttl=3600)
            return False
        else:
            async with authed_services_lock:
                authed_services.setdefault(self.client.me.id, {})[service] = True
                await self.client.cache.set(cache_key, True, ttl=3600)
            return True

    async def _show_super_ad(self):
        async with super_ad_shown_lock:
            user_super_ad_shown = super_ad_shown.get(self.client.me.id, False)
            if not user_super_ad_shown:
                self.log.info("请访问 https://go.zetx.tech/eksuper 赞助项目以升级为高级用户, 尊享更多功能.")
                super_ad_shown[self.client.me.id] = True
                return True
            else:
                return False

    async def captcha(self, site: str, url: str = None):
        """向机器人发送验证码解析请求."""
        cache_key = f"captcha_{site}_{url}_{self.instance}"
        cached_result = await self.client.cache.get(cache_key)
        if cached_result:
            return cached_result.get("token", None)

        cmd = f"/captcha {self.instance} {site}"
        if url:
            cmd += f" {url}"
        results = await self.post(cmd, timeout=240, name="请求跳过验证码")
        if results:
            await self.client.cache.set(cache_key, results, ttl=300)  # 缓存5分钟
            return results.get("token", None)
        return None

    async def captcha_url(self, site: str, url: str = None):
        """向机器人发送带验证码的远程网页解析请求."""
        cache_key = f"captcha_url_{site}_{url}_{self.instance}"
        cached_result = await self.client.cache.get(cache_key)
        if cached_result:
            return cached_result.get("cf_clearance", None), cached_result.get("result", None)

        cmd = f"/captcha {self.instance} {site}"
        if url:
            cmd += f" {url}"
        results = await self.post(cmd, timeout=240, name="请求跳过验证码")
        if results:
            await self.client.cache.set(cache_key, results, ttl=300)  # 缓存5分钟
            return results.get("cf_clearance", None), results.get("result", None)
        return None, None

    async def captcha_emby(self, url: str):
        """向机器人发送带验证码的远程网页解析请求."""
        cache_key = f"captcha_emby_{url}_{self.instance}"
        cached_result = await self.client.cache.get(cache_key)
        if cached_result:
            return cached_result.get("cf_clearance", None), cached_result.get("proxy", None)

        cmd = f"/captcha {self.instance} emby {url}"
        results = await self.post(cmd, timeout=240, name="请求跳过验证码")
        if results:
            await self.client.cache.set(cache_key, results, ttl=300)  # 缓存5分钟
            return results.get("cf_clearance", None), results.get("proxy", None)
        return None, None

    async def pornemby_answer(self, question: str):
        """向机器人发送问题回答请求."""
        cache_key = f"pornemby_answer_{question}_{self.instance}"
        cached_result = await self.client.cache.get(cache_key)
        if cached_result:
            return cached_result.get("answer", None), cached_result.get("by", None)

        results = await self.post(
            f"/pornemby_answer {self.instance} {question}", 
            timeout=20, 
            name="请求问题回答"
        )
        if results:
            await self.client.cache.set(cache_key, results, ttl=3600)  # 缓存1小时
            return results.get("answer", None), results.get("by", None)
        return None, None

    async def terminus_answer(self, question: str):
        """向机器人发送问题回答请求."""
        cache_key = f"terminus_answer_{question}_{self.instance}"
        cached_result = await self.client.cache.get(cache_key)
        if cached_result:
            return cached_result.get("answer", None), cached_result.get("by", None)

        results = await self.post(
            f"/terminus_answer {self.instance} {question}", 
            timeout=20, 
            name="请求问题回答"
        )
        if results:
            await self.client.cache.set(cache_key, results, ttl=3600)  # 缓存1小时
            return results.get("answer", None), results.get("by", None)
        return None, None

    async def gpt(self, prompt: str):
        """向机器人发送智能回答请求."""
        cache_key = f"gpt_{prompt}_{self.instance}"
        cached_result = await self.client.cache.get(cache_key)
        if cached_result:
            return cached_result.get("answer", None), cached_result.get("by", None)

        results = await self.post(
            f"/gpt {self.instance} {prompt}", 
            timeout=20, 
            name="请求智能回答"
        )
        if results:
            await self.client.cache.set(cache_key, results, ttl=3600)  # 缓存1小时
            return results.get("answer", None), results.get("by", None)
        return None, None

    async def visual(self, photo, options: List[str], question=None):
        """向机器人发送视觉问题解答请求."""
        # 视觉问题不缓存，因为图片内容可能变化
        cmd = f"/visual {self.instance} {'/'.join(options)}"
        if question:
            cmd += f" {question}"
        results = await self.post(
            cmd, 
            photo=photo, 
            timeout=20, 
            name="请求视觉问题解答"
        )
        if results:
            return results.get("answer", None), results.get("by", None)
        return None, None

    async def ocr(self, photo):
        """向机器人发送 OCR 解答请求."""
        # OCR 不缓存，因为图片内容可能变化
        cmd = f"/ocr {self.instance}"
        results = await self.post(
            cmd, 
            photo=photo, 
            timeout=20, 
            name="请求验证码解答"
        )
        if results:
            return results.get("answer", None)
        return None

    async def send_log(self, message):
        """向机器人发送日志记录请求."""
        # 日志不缓存
        results = await self.post(
            f"/log {self.instance} {message}", 
            name="发送日志到 Telegram"
        )
        return bool(results)

    async def send_msg(self, message):
        """向机器人发送即时日志记录请求."""
        # 即时消息不缓存
        results = await self.post(
            f"/msg {self.instance} {message}", 
            name="发送即时日志到 Telegram"
        )
        return bool(results)
