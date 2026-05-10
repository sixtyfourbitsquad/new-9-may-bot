"""Redis-backed finite state for admin panel wizards (private chat)."""

from __future__ import annotations

import json
from typing import Any, Optional

from redis.asyncio import Redis


# State machine keys (stored JSON: {"state": str, ...})
STATE_BC_WAIT_MSG = "bc_msg"
STATE_BC_WAIT_BUTTONS_JSON = "bc_kb"
STATE_SCH_WAIT_FIRST_HOURS = "sch_h_first"
STATE_SCH_WAIT_REPEAT_HOURS = "sch_h_repeat"
STATE_SCH_WAIT_BODY = "sch_body"
STATE_SCH_WAIT_KB = "sch_kb"
STATE_WM_WAIT = "wm_body"
STATE_WM_WAIT_KB = "wm_kb"
STATE_WM_BATCH = "wm_batch"
STATE_RM_WAIT_HOURS = "rm_hours"
STATE_RM_WAIT_BODY = "rm_body"
STATE_RM_WAIT_KB = "rm_kb"
STATE_CH_WAIT_ID = "ch_id"
STATE_OD_WAIT_BODY = "od_body"
STATE_OD_WAIT_KB = "od_kb"
STATE_LS_WAIT_TEMPLATE = "ls_tpl"
STATE_LS_WAIT_MANUAL_URL = "ls_manual"
STATE_AD_WAIT_ID = "ad_id"


class AdminFsm:
    """Thin helper around Redis keys."""

    def __init__(self, redis: Redis, fsm_prefix: str) -> None:
        self._r = redis
        self._p = fsm_prefix.rstrip(":") + ":" if not fsm_prefix.endswith(":") else fsm_prefix

    def key_fsm(self, user_id: int) -> str:
        return f"{self._p}{user_id}"

    def key_draft_broadcast(self, user_id: int) -> str:
        return f"{self._p}draft_bc:{user_id}"

    async def get(self, user_id: int) -> Optional[dict[str, Any]]:
        raw = await self._r.get(self.key_fsm(user_id))
        if not raw:
            return None
        return json.loads(raw)

    async def set(self, user_id: int, data: dict[str, Any], ttl: int = 7200) -> None:
        await self._r.set(self.key_fsm(user_id), json.dumps(data), ex=ttl)

    async def clear(self, user_id: int) -> None:
        await self._r.delete(self.key_fsm(user_id))

    async def set_draft_broadcast(self, user_id: int, payload: dict[str, Any]) -> None:
        await self._r.set(self.key_draft_broadcast(user_id), json.dumps(payload), ex=86400)

    async def get_draft_broadcast(self, user_id: int) -> Optional[dict[str, Any]]:
        raw = await self._r.get(self.key_draft_broadcast(user_id))
        if not raw:
            return None
        return json.loads(raw)

    async def clear_draft_broadcast(self, user_id: int) -> None:
        await self._r.delete(self.key_draft_broadcast(user_id))
