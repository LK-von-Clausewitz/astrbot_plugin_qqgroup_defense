import re
from typing import Dict, Set

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.message_components import At, Plain
from astrbot.core.star.filter.event_message_type import EventMessageType


class GroupDefensePlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.threshold = self.config.get("threshold", 2)
        self.report_keyword = self.config.get("reportKeyword", "有内鬼")
        self.reports: Dict[str, Set[str]] = {}
        logger.info(f"[群防御] 插件加载成功。")

    def _extract_target_from_message(self, event: AstrMessageEvent) -> str | None:
        message_chain = event.message_obj.message
        if not message_chain: return None
        keyword_seen = False
        for comp in message_chain:
            if isinstance(comp, Plain):
                if self.report_keyword in comp.text:
                    keyword_seen = True
                    after_text = comp.text[comp.text.find(self.report_keyword) + len(self.report_keyword):]
                    match_num = re.search(r"^\s*(\d+)", after_text)
                    if match_num: return str(match_num.group(1)).strip()
            elif isinstance(comp, At):
                if keyword_seen: return str(comp.qq).strip()
        return None

    @filter.event_message_type(EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent):
        message_obj = event.message_obj
        if not message_obj.group_id: return
        if not event.message_str.strip().startswith(self.report_keyword): return

        # --- 1. 尝试所有可能路径获取机器人自己的 QQ 号 ---
        self_id = ""
        try:
            if hasattr(event, "bot_id") and event.bot_id:
                self_id = str(event.bot_id)
            elif hasattr(event, "bot") and hasattr(event.bot, "self_id"):
                self_id = str(event.bot.self_id)
            elif hasattr(message_obj, "self_id"):
                self_id = str(message_obj.self_id)
            
            # 如果还是空，尝试从 context 获取（最后的兜底）
            if not self_id:
                # 注意：有些版本的 AstrBot 无法简单获取 self_id，这里记录一下
                logger.warning("[群防御] 无法自动获取机器人ID，自保逻辑可能失效")
        except:
            pass

        # 2. 提取被举报人 ID 和 举报人 ID
        target_id = self._extract_target_from_message(event)
        sender_id = str(message_obj.sender.user_id).strip()
        
        if not target_id:
            yield event.plain_result(f"❌ 请使用正确格式：{self.report_keyword} @用户")
            return

        target_id = str(target_id).strip() # 再次强制去空格
        self_id = str(self_id).strip() if self_id else ""

        # --- 3. 核心优先级：自保 > 自残 > 管理员 ---

        # A. 自保：只要 target_id 等于机器人 ID，直接回绝，不再往下走
        if self_id and target_id == self_id:
            yield event.plain_result("😅 怎么滴，还想把我也端了？")
            return

        # B. 自残：不能自己报自己
        if target_id == sender_id:
            yield event.plain_result("❌ 为什么要举报你自己？")
            return

        # C. 管理员拦截
        try:
            target_info = await event.bot.get_group_member_info(
                group_id=int(message_obj.group_id), 
                user_id=int(target_id)
            )
            # 如果是管理员或群主，拦截
            if target_info and target_info.get("role") in ["admin", "owner"]:
                yield event.plain_result("⚠️ 对方是管理员或群主，我踢不动。")
                return
        except Exception as e:
            # 这里的报错通常是因为 target_id 根本不在群里或者是机器人自己
            logger.debug(f"检查权限异常: {e}")

        # --- 4. 正常的计数逻辑 ---
        if target_id not in self.reports:
            self.reports[target_id] = set()

        reporters = self.reports[target_id]
        if sender_id in reporters:
            yield event.plain_result("⚠️ 你已经举报过该用户了。")
            return

        reporters.add(sender_id)
        current_count = len(reporters)
        yield event.plain_result(f"📢 收到举报！目标：{target_id}\n进度：{current_count}/{self.threshold}")

        if current_count >= self.threshold:
            try:
                await event.bot.call_action(
                    "set_group_kick",
                    group_id=int(message_obj.group_id),
                    user_id=int(target_id)
                )
                self.reports.pop(target_id, None)
                yield event.plain_result(f"🚫 用户 {target_id} 举报达标，已移出。")
            except:
                yield event.plain_result("❌ 踢出失败，可能我没有管理员权限。")

    async def terminate(self):
        self.reports.clear()
