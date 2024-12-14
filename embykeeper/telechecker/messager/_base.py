import asyncio
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, List, Union, Dict, Any, Optional as OptionalType

import yaml
from dateutil import parser
from loguru import logger
from telethon import types, errors
from schema import Optional, Schema, SchemaError

from ...data import get_data
from ...var import debug
from ...utils import show_exception, truncate_str, distribute_numbers
from ..tele import ClientsSession
from ..link import Link

__ignore__ = True


@dataclass(eq=False)
class _MessageSchedule:
    """定义一个发送规划, 即在特定时间段内某些消息中的一个有一定几率被发送."""

    messages: Iterable[str]
    at: Union[Iterable[Union[str, time]], Union[str, time]] = ("0:00", "23:59")
    possibility: float = 1.0
    multiply: int = 1
    only: str = None


@dataclass(eq=False)
class MessageSchedule:
    """定义一个发送规划, 即在特定时间段内某些消息中的一个有一定几率被发送, 允许使用一个话术列表资源名作为基础配置."""

    spec: str = None
    messages: Iterable[str] = None
    at: Union[Iterable[Union[str, time]], Union[str, time]] = None
    possibility: float = None
    only: str = None
    multiply: int = None

    def to_message_schedule(self) -> _MessageSchedule:
        return _MessageSchedule(
            messages=self.messages,
            at=self.at or ("0:00", "23:59"),
            possibility=self.possibility or 1,
            multiply=self.multiply or 1,
            only=self.only,
        )


@dataclass(eq=False)
class MessagePlan:
    """定义一个发送计划, 即在某事件发送某个消息."""

    message: str
    at: datetime
    schedule: _MessageSchedule
    skip: bool = False


class Messager:
    """自动水群类."""

    name: str = None  # 水群器名称
    chat_name: str = None  # 群聊的名称
    default_messages: List[Union[str, MessageSchedule]] = []  # 默认的话术列表资源名
    additional_auth: List[str] = []  # 额外认证要求
    min_interval: int = None  # 预设两条消息间的最小间隔时间
    max_interval: int = None  # 预设两条消息间的最大间隔时间

    # 使用类级别的缓存和锁
    _site_cache: Dict[int, Dict[str, Any]] = {}
    _site_locks: Dict[int, asyncio.Lock] = {}
    _message_cache: Dict[int, Dict[str, Any]] = {}
    _client_sessions: Dict[int, ClientsSession] = {}

    def __init__(self, account, me: types.User = None, nofail=True, proxy=None, basedir=None, config: dict = None):
        self.account = account
        self.nofail = nofail
        self.proxy = proxy
        self.basedir = basedir
        self.config = config
        self.me = me

        self.min_interval = config.get(
            "min_interval", config.get("interval", self.min_interval or 60)
        )
        self.max_interval = config.get("max_interval", self.max_interval)
        self.log = logger.bind(scheme="telemessager", name=self.name, username=me.first_name)
        self.timeline: List[MessagePlan] = []

        # 初始化用户特定的缓存和锁
        if me.id not in self._site_cache:
            self._site_cache[me.id] = {}
        if me.id not in self._site_locks:
            self._site_locks[me.id] = asyncio.Lock()
        if me.id not in self._message_cache:
            self._message_cache[me.id] = {}

    async def get_client_session(self) -> OptionalType[ClientsSession]:
        """获取或创建客户端会话"""
        if self.me.id not in self._client_sessions:
            session = ClientsSession(
                [self.account], 
                proxy=self.proxy, 
                basedir=self.basedir
            )
            await session.__aenter__()
            self._client_sessions[self.me.id] = session
        return self._client_sessions[self.me.id]

    async def close_client_session(self):
        """关闭客户端会话"""
        if self.me.id in self._client_sessions:
            session = self._client_sessions[self.me.id]
            await session.__aexit__(None, None, None)
            del self._client_sessions[self.me.id]

    async def send_message(self, chat_id: Union[int, str], message: str) -> bool:
        """发送消息的包装方法，包含重试和错误处理"""
        session = await self.get_client_session()
        async for client in session:
            for retry in range(3):  # 最多重试3次
                try:
                    await client.send_message(chat_id, message)
                    return True
                except errors.FloodWaitError as e:
                    if retry < 2:  # 最后一次重试不等待
                        await asyncio.sleep(e.seconds)
                    continue
                except errors.ChatWriteForbiddenError:
                    self.log.warning(f"无法在群组中发送消息，可能被禁言或未加入群组")
                    return False
                except errors.SlowModeWaitError as e:
                    if retry < 2:
                        await asyncio.sleep(e.seconds)
                    continue
                except Exception as e:
                    self.log.error(f"发送消息时发生错误: {e}")
                    if retry == 2:
                        return False
                    await asyncio.sleep(1)
        return False

    async def start(self):
        """自动水群器的入口函数"""
        try:
            session = await self.get_client_session()
            async for client in session:
                # 验证群组访问权限
                try:
                    chat = await client._client.get_entity(self.chat_name)
                    if isinstance(chat, types.Channel):
                        try:
                            await client._client.get_permissions(chat)
                        except errors.ChatAdminRequiredError:
                            self.log.info(f'跳过水群: 尚未加入群组 "{chat.title}".')
                            return False
                except errors.UsernameNotOccupiedError:
                    self.log.warning(f'初始化错误: 群组 "{self.chat_name}" 不存在.')
                    return False

                # 验证额外权限
                if self.additional_auth:
                    for auth in self.additional_auth:
                        if not await Link(client).auth(auth, log_func=self.log.info):
                            return False

                # 初始化消息计划
                if not await self.init():
                    self.log.warning(f"消息计划初始化失败，水群器将停止.")
                    return False

                # 执行消息计划
                return await self.run_message_plans()

        except errors.FloodWaitError as e:
            self.log.info(f"初始化信息: Telegram 要求等待 {e.seconds} 秒.")
            if e.seconds < 360:
                await asyncio.sleep(e.seconds)
                return await self.start()  # 重试
            self.log.info(f"等待时间过长，水群器将停止.")
            return False

        except Exception as e:
            if self.nofail:
                self.log.warning(f"发生错误，水群器将停止.")
                show_exception(e, regular=False)
                return False
            raise

        finally:
            await self.close_client_session()

    async def run_message_plans(self):
        """执行消息计划"""
        if not self.timeline:
            self.log.warning("没有可执行的消息计划.")
            return False

        success_count = 0
        total_count = len(self.timeline)

        for plan in self.timeline:
            if plan.skip:
                continue

            now = datetime.now()
            if plan.at > now:
                wait_time = (plan.at - now).total_seconds()
                await asyncio.sleep(wait_time)

            if await self.send_message(self.chat_name, plan.message):
                success_count += 1
                self.log.info(f"消息发送成功 ({success_count}/{total_count})")
            else:
                self.log.warning(f"消息发送失败 ({success_count}/{total_count})")

        return success_count > 0

    async def init(self):
        """初始化函数，可被子类重写"""
        return True

    def parse_message_yaml(self, file):
        """解析话术文件"""
        try:
            with open(file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            schema = Schema(
                {
                    "messages": [str],
                    Optional("at"): [str],
                    Optional("possibility"): float,
                    Optional("only"): str,
                }
            )
            schema.validate(data)
            at = data.get("at", ("9:00", "23:00"))
            assert len(at) == 2
            at = [parser.parse(t).time() for t in at]
            return _MessageSchedule(
                messages=data.get("messages"),
                at=at,
                possibility=data.get("possibility", 1.0),
                only=data.get("only", None),
            )
        except (yaml.YAMLError, SchemaError) as e:
            self.log.error(f"解析话术文件失败: {e}")
            return None
        except Exception as e:
            self.log.error(f"读取话术文件时发生错误: {e}")
            return None

    async def prepare_message_schedules(self):
        """准备消息计划"""
        if self.max_interval and self.min_interval > self.max_interval:
            self.log.warning(f"配置错误: 最小间隔不应大于最大间隔")
            return []

        messages = self.config.get("messages", []) or self.default_messages
        schedules = []

        for m in messages:
            schedule = None
            if isinstance(m, MessageSchedule):
                schedule = await self.get_spec_schedule(m)
            else:
                match = re.match(r"(.*)\*\s?(\d+)", m)
                if match:
                    multiply = int(match.group(2))
                    spec = match.group(1).strip()
                else:
                    multiply = 1
                    spec = m
                schedule = await self.get_spec_schedule(spec)
                if schedule:
                    schedule.multiply = multiply

            if schedule:
                schedules.append(schedule)

        return schedules

    async def generate_timeline(self, schedules: List[_MessageSchedule]):
        """生成消息时间线"""
        self.timeline.clear()
        total_messages = sum(s.multiply for s in schedules)
        self.log.info(f"正在生成时间线: {len(schedules)} 个消息规划, 共 {total_messages} 条消息")

        for schedule in schedules:
            if not await self.add(schedule, use_multiply=True):
                self.log.warning(f"部分消息计划生成失败")

        if self.timeline:
            self.timeline.sort(key=lambda x: x.at)
            next_valid = next((p for p in self.timeline if not p.skip), None)
            if next_valid:
                self.log.info(
                    f"首次发送将在 [blue]{next_valid.at.strftime('%m-%d %H:%M:%S')}[/] 进行: {truncate_str(next_valid.message, 20)}"
                )
        return bool(self.timeline)

    async def add(self, schedule: _MessageSchedule, use_multiply=False) -> bool:
        """添加消息计划到时间线"""
        try:
            start_time, end_time = schedule.at
            if isinstance(start_time, str):
                start_time = parser.parse(start_time).time()
            if isinstance(end_time, str):
                end_time = parser.parse(end_time).time()

            start_datetime = datetime.combine(date.today(), start_time or time(0, 0))
            end_datetime = datetime.combine(date.today(), end_time or time(23, 59, 59))
            if end_datetime < start_datetime:
                end_datetime += timedelta(days=1)

            start_timestamp = start_datetime.timestamp()
            end_timestamp = end_datetime.timestamp()
            num_plans = schedule.multiply if use_multiply else 1

            # 获取现有时间点
            base = [mp.at.timestamp() for mp in self.timeline]
            
            # 生成新的时间点
            timestamps = distribute_numbers(
                start_timestamp, 
                end_timestamp, 
                num_plans, 
                self.min_interval, 
                self.max_interval, 
                base=base
            )

            # 创建消息计划
            for t in timestamps:
                at = datetime.fromtimestamp(t)
                if at < datetime.now():
                    at += timedelta(days=1)

                skip = random.random() >= schedule.possibility
                if not skip and schedule.only:
                    today = datetime.today()
                    if schedule.only.startswith("weekday") and today.weekday() > 4:
                        skip = True
                    elif schedule.only.startswith("weekend") and today.weekday() < 5:
                        skip = True

                self.timeline.append(
                    MessagePlan(
                        message=random.choice(schedule.messages),
                        at=at,
                        schedule=schedule,
                        skip=skip,
                    )
                )

            return True

        except Exception as e:
            self.log.error(f"添加消息计划时发生错误: {e}")
            return False

    async def get_spec_path(self, spec: str) -> OptionalType[str]:
        """获取话术文件路径"""
        try:
            if Path(spec).exists():
                return spec
            return await get_data(self.basedir, spec, proxy=self.proxy, caller=f"{self.name}水群")
        except Exception as e:
            self.log.error(f"获取话术文件路径失败: {e}")
            return None

    async def get_spec_schedule(self, spec_or_schedule: Union[str, MessageSchedule]) -> OptionalType[_MessageSchedule]:
        """获取消息计划"""
        try:
            if isinstance(spec_or_schedule, MessageSchedule):
                if spec_or_schedule.spec:
                    file = await self.get_spec_path(spec_or_schedule.spec)
                    if not file:
                        self.log.warning(f'话术文件 "{spec_or_schedule.spec}" 无法访问')
                        return None

                    base_schedule = self.parse_message_yaml(file)
                    if not base_schedule:
                        return None

                    # 合并配置
                    return _MessageSchedule(
                        messages=spec_or_schedule.messages or base_schedule.messages,
                        at=spec_or_schedule.at or base_schedule.at,
                        possibility=spec_or_schedule.possibility or base_schedule.possibility,
                        multiply=spec_or_schedule.multiply or base_schedule.multiply,
                        only=spec_or_schedule.only or base_schedule.only,
                    )
                return spec_or_schedule.to_message_schedule()
            else:
                file = await self.get_spec_path(spec_or_schedule)
                if not file:
                    self.log.warning(f'话术文件 "{spec_or_schedule}" 无法访问')
                    return None
                return self.parse_message_yaml(file)

        except Exception as e:
            self.log.error(f"获取消息计划失败: {e}")
            return None

    async def execute_timeline(self):
        """执行消息时间线"""
        while self.timeline:
            # 获取下一个计划
            next_plan = min(self.timeline, key=lambda x: x.at)
            wait_time = (next_plan.at - datetime.now()).total_seconds()
            
            if wait_time > 0:
                try:
                    await asyncio.sleep(wait_time)
                except asyncio.CancelledError:
                    self.log.info("消息计划执行被取消")
                    return False

            # 发送消息
            if not next_plan.skip:
                if not await self.send_message(self.chat_name, next_plan.message):
                    self.log.warning("消息发送失败，将重试下一条消息")

            # 更新时间线
            self.timeline.remove(next_plan)
            await self.add(next_plan.schedule)
            
            # 重新排序时间线
            self.timeline.sort(key=lambda x: x.at)

        return True

    async def run(self):
        """运行消息计划"""
        try:
            # 准备消息计划
            schedules = await self.prepare_message_schedules()
            if not schedules:
                self.log.warning("没有可用的消息计划")
                return False

            # 生成时间线
            if not await self.generate_timeline(schedules):
                self.log.warning("生成时间线失败")
                return False

            # 执行时间线
            return await self.execute_timeline()

        except Exception as e:
            self.log.error(f"执行消息计划时发生错误: {e}")
            return False
