import asyncio
import logging
from typing import Dict, Any, Optional, Union
from functools import lru_cache

from loguru import logger

from ..log import formatter
from .log import TelegramStream

logger = logger.bind(scheme="telegram")

# 缓存通知器配置
_notifier_cache: Dict[str, Any] = {}
_stream_cache: Dict[str, TelegramStream] = {}
_lock = asyncio.Lock()

@lru_cache(maxsize=128)
def _get_cached_filter(filter_type: str):
    """获取缓存的过滤器函数"""
    if filter_type == 'log':
        def _filter_log(record):
            notify = record.get("extra", {}).get("log", None)
            return bool(notify or record["level"].no == logging.ERROR)
        return _filter_log
    elif filter_type == 'msg':
        def _filter_msg(record):
            notify = record.get("extra", {}).get("msg", None)
            return bool(notify)
        return _filter_msg
    return None

@lru_cache(maxsize=1)
def _get_formatter():
    """获取缓存的格式化函数"""
    def _formatter(record):
        return "{level}#" + formatter(record)
    return _formatter

def _get_account_by_identifier(accounts: list, identifier: Union[bool, int, str]) -> Optional[dict]:
    """根据标识符获取账号信息"""
    try:
        if identifier is True:
            return accounts[0]
        elif isinstance(identifier, int):
            return accounts[identifier + 1]
        elif isinstance(identifier, str):
            return next((a for a in accounts if a["phone"] == identifier), None)
        return None
    except (IndexError, KeyError):
        return None

async def get_telegram_stream(account: dict, config: dict, instant: bool = False) -> TelegramStream:
    """获取或创建 TelegramStream 实例"""
    cache_key = f"{account['phone']}_{instant}"
    async with _lock:
        if cache_key not in _stream_cache:
            stream = TelegramStream(
                account=account,
                proxy=config.get("proxy"),
                basedir=config.get("basedir"),
                instant=instant
            )
            _stream_cache[cache_key] = stream
        return _stream_cache[cache_key]

async def cleanup_streams():
    """清理并关闭所有流"""
    async with _lock:
        for stream in _stream_cache.values():
            try:
                await stream.cleanup()
            except Exception as e:
                logger.error(f"清理通知流时发生错误: {e}")
        _stream_cache.clear()

async def start_notifier(config: dict) -> bool:
    """
    消息通知初始化函数
    
    Args:
        config: 配置字典，包含 telegram 账号和通知设置
        
    Returns:
        bool: 通知器是否成功启动
    """
    try:
        accounts = config.get("telegram", [])
        notifier_id = config.get("notifier")
        
        if not notifier_id:
            return False
            
        # 获取通知账号
        notifier = _get_account_by_identifier(accounts, notifier_id)
        if not notifier:
            logger.warning("未找到有效的通知账号")
            return False
            
        # 缓存通知器配置
        cache_key = notifier["phone"]
        if cache_key in _notifier_cache:
            return True
            
        logger.info(f'计划任务的关键消息将通过 Embykeeper Bot 发送至 "{notifier["phone"]}" 账号')
        
        # 添加日志流
        log_stream = await get_telegram_stream(
            notifier, 
            config, 
            instant=config.get("notify_immediately", False)
        )
        logger.add(
            log_stream,
            format=_get_formatter(),
            filter=_get_cached_filter('log'),
            catch=True
        )
        
        # 添加消息流
        msg_stream = await get_telegram_stream(
            notifier, 
            config, 
            instant=True
        )
        logger.add(
            msg_stream,
            format=_get_formatter(),
            filter=_get_cached_filter('msg'),
            catch=True
        )
        
        # 缓存通知器配置
        _notifier_cache[cache_key] = {
            'log_stream': log_stream,
            'msg_stream': msg_stream,
            'config': config
        }
        
        return True
        
    except Exception as e:
        logger.error(f"初始化通知器时发生错误: {e}")
        return False
        
    finally:
        # 清理未使用的缓存
        async with _lock:
            current_phones = {a["phone"] for a in accounts if a}
            cached_phones = set(_notifier_cache.keys())
            for phone in cached_phones - current_phones:
                if phone in _notifier_cache:
                    del _notifier_cache[phone]
