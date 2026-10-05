"""按指定版面日期恢复留存数据；缺少记录时仅补可核实的历史行情。"""
from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from datetime import date, datetime, time
from types import UnionType
from typing import Union, get_args, get_origin, get_type_hints

from .archive import Archive, plain
from .publication import InsufficientData, publication_issue


def restore(model, value):
    """模型由调用方指定，归档内容不能指定类名或执行代码。"""
    if value is None:
        return None
    origin, args = get_origin(model), get_args(model)
    if origin in (Union, UnionType):
        return restore(next(item for item in args if item is not type(None)), value)
    if model in (date, datetime):
        return model.fromisoformat(value)
    if origin is list:
        return [restore(args[0], item) for item in value]
    if origin is dict:
        return {key: restore(args[1], item) for key, item in value.items()}
    if is_dataclass(model):
        hints = get_type_hints(model)
        return model(**{field.name: restore(hints[field.name], value[field.name])
                        for field in fields(model) if field.name in value})
    return value


def dated_brief(archive: Archive, market: str, kind: str, day: date, now: datetime):
    if market == "ashare":
        from ashare.compose import build_brief
        from ashare.history import load_history
        from ashare.models import CST as zone, Brief, MarketData
    else:
        from usstock.compose import build_brief
        from usstock.history import load_history
        from usstock.models import NY as zone, Brief, MarketData

    end = datetime.combine(day, time.max, zone)

    def eligible(brief):
        return (brief.kind == kind and brief.edition_date == day and brief.generated_at <= now
                and not publication_issue(market, plain(brief))
                and not (market == "ashare" and kind == "close" and brief.is_intraday))

    # 文件遗失不影响恢复已留存的版面数据；重新绘图仍走正常事务发布。
    for row in archive.reports():
        if (row["market"], row["session"], row["edition_date"]) == (market, kind, day.isoformat()):
            brief = restore(Brief, row["data"])
            if brief.generated_at <= end and eligible(brief):
                return brief, row["capture_id"]

    # 采集成功、绘图失败时，按原采集时间恢复，不混入事后的消息。
    for capture in reversed(archive.snapshots(through=now)):
        if capture["market"] != market or capture["data"].get("origin") == "backfill":
            continue
        payload = capture['data']
        data = restore(MarketData, payload)
        reference = datetime.fromisoformat(capture["first_seen"])
        if payload.get('origin') == 'history':
            if (payload['session'], payload['edition_date']) != (kind, day.isoformat()):
                continue
            reference = datetime.fromisoformat(payload['reference_at'])
        elif reference > end:
            if kind in {'morning', 'premarket'}:
                continue
            # 稍后采集的日线可以恢复收盘，之后的消息与无日期资金/广度不能冒充当日。
            reference = end
            notes = data.notes + ['历史收盘从稍后留存快照恢复；只保留当日可核验行情，不采用之后的新闻']
            notes.append(f"实际采集{datetime.fromisoformat(capture['first_seen']).astimezone(zone):%Y-%m-%d %H:%M %Z}；参考时点为指定日期收盘后")
            if market == 'ashare':
                data = replace(data, sectors_up=[], sectors_down=[], sector_in=[], sector_out=[], breadth=None,
                               overseas=[], fx=[], capital=[item for item in data.capital if item.trade_day == day.isoformat()],
                               cross=data.cross if data.cross and data.cross.trade_day == day.isoformat() else None,
                               notes=notes)
            else:
                data = replace(data, notes=notes)
            data.news = [item for item in data.news if item.published.tzinfo is not None and item.published <= end]
        brief = build_brief(kind, data, now=reference)
        if eligible(brief):
            return brief, capture["id"]

    data, reference = load_history(kind, day, now)
    data.notes.append(f"实际采集{now.astimezone(zone):%Y-%m-%d %H:%M %Z}；历史参考时点{reference:%Y-%m-%d %H:%M %Z}")
    capture_id = archive.capture(market, plain(data) | {'origin': 'history', 'session': kind,
                                 'edition_date': day.isoformat(), 'reference_at': reference.isoformat()}, now)
    brief = build_brief(kind, data, now=reference)
    if not eligible(brief):
        raise InsufficientData(f"{day} {kind} 的历史核心行情不足，无法生成指定日期")
    return brief, capture_id
