# 该文件用于同机器人 Messager, Monitor 和 Bots 之间的异步锁和通讯

import asyncio
from typing import Dict, List, Tuple, Any
from datetime import datetime
from cachetools import TTLCache
from aiocache import Cache

# OCR相关
class OCRCache:
    def __init__(self):
        self._cache = TTLCache(maxsize=1024, ttl=3600)  # 1小时过期
        self._lock = asyncio.Lock()
    
    async def get(self, key: str) -> Tuple[Any, bool]:
        async with self._lock:
            return self._cache.get(key, (None, False))
    
    async def set(self, key: str, value: Tuple[Any, bool]):
        async with self._lock:
            self._cache[key] = value

ocr_cache = OCRCache()

# 监控器相关
class MonitorManager:
    def __init__(self):
        self._monitors: Dict[int, Any] = {}  # uid: Monitor实例
        self._locks: Dict[int, asyncio.Lock] = {}  # uid: Lock实例
    
    def get_monitor(self, uid: int) -> Any:
        return self._monitors.get(uid)
    
    def set_monitor(self, uid: int, monitor: Any):
        self._monitors[uid] = monitor
    
    def get_lock(self, uid: int) -> asyncio.Lock:
        if uid not in self._locks:
            self._locks[uid] = asyncio.Lock()
        return self._locks[uid]

misty_manager = MonitorManager()

# Pornemby相关
class PornembyState:
    def __init__(self):
        self._nohp: Dict[int, datetime] = {}  # uid: date
        self._messager_enabled: Dict[int, bool] = {}  # uid: bool
        self._alert: Dict[int, bool] = {}  # uid: bool
        self._messager_mids: Dict[int, List[int]] = {}  # uid: list(mid)
        self._lock = asyncio.Lock()
    
    async def get_nohp(self, uid: int) -> datetime:
        async with self._lock:
            return self._nohp.get(uid)
    
    async def set_nohp(self, uid: int, date: datetime):
        async with self._lock:
            self._nohp[uid] = date
    
    async def get_messager_enabled(self, uid: int) -> bool:
        async with self._lock:
            return self._messager_enabled.get(uid, False)
    
    async def set_messager_enabled(self, uid: int, enabled: bool):
        async with self._lock:
            self._messager_enabled[uid] = enabled
    
    async def get_alert(self, uid: int) -> bool:
        async with self._lock:
            return self._alert.get(uid, False)
    
    async def set_alert(self, uid: int, alert: bool):
        async with self._lock:
            self._alert[uid] = alert
    
    async def get_messager_mids(self, uid: int) -> List[int]:
        async with self._lock:
            return self._messager_mids.get(uid, [])
    
    async def add_messager_mid(self, uid: int, mid: int):
        async with self._lock:
            if uid not in self._messager_mids:
                self._messager_mids[uid] = []
            self._messager_mids[uid].append(mid)
    
    async def clear_messager_mids(self, uid: int):
        async with self._lock:
            self._messager_mids[uid] = []

pornemby_state = PornembyState()

# 广告和服务认证相关
class AuthState:
    def __init__(self):
        self._super_ad_shown: Dict[int, bool] = {}  # uid: bool
        self._super_ad_lock = asyncio.Lock()
        self._authed_services: Dict[int, Dict[str, bool]] = {}  # uid: {service: bool}
        self._services_lock = asyncio.Lock()
        self._cache = Cache()  # 使用aiocache进行缓存
    
    async def is_ad_shown(self, uid: int) -> bool:
        async with self._super_ad_lock:
            return self._super_ad_shown.get(uid, False)
    
    async def set_ad_shown(self, uid: int, shown: bool):
        async with self._super_ad_lock:
            self._super_ad_shown[uid] = shown
    
    async def get_service_auth(self, uid: int, service: str) -> bool:
        # 先尝试从缓存获取
        cache_key = f"auth_{service}_{uid}"
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return cached
        
        # 缓存未命中，从内存获取
        async with self._services_lock:
            result = self._authed_services.get(uid, {}).get(service, False)
            # 写入缓存
            await self._cache.set(cache_key, result, ttl=3600)
            return result
    
    async def set_service_auth(self, uid: int, service: str, authed: bool):
        cache_key = f"auth_{service}_{uid}"
        async with self._services_lock:
            if uid not in self._authed_services:
                self._authed_services[uid] = {}
            self._authed_services[uid][service] = authed
            # 更新缓存
            await self._cache.set(cache_key, authed, ttl=3600)

auth_state = AuthState()

# 导出变量，保持向后兼容
ocrs = ocr_cache._cache
ocrs_lock = ocr_cache._lock
misty_monitors = misty_manager._monitors
misty_locks = misty_manager._locks
pornemby_nohp = pornemby_state._nohp
pornemby_messager_enabled = pornemby_state._messager_enabled
pornemby_alert = pornemby_state._alert
pornemby_messager_mids = pornemby_state._messager_mids
super_ad_shown = auth_state._super_ad_shown
super_ad_shown_lock = auth_state._super_ad_lock
authed_services = auth_state._authed_services
authed_services_lock = auth_state._services_lock
