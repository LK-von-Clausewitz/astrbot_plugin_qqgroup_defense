import re
from typing import Dict, Set

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.message_components import At, Plain
from astrbot.core.star.filter.event_message_type import EventMessageType


class GroupDefensePlugin(Star):
    """
    QQ 群防御插件
    监听群消息中的举报指令，累计举报人数，达到阈值后自动踢出被举报用户
    """

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.threshold = self.config.get("threshold", 2)
        self.report_keyword = self.config.get("reportKeyword", "有内鬼")

        # 举报记录：target_id -> Set[reporter_id]
        self.reports: Dict[str, Set[str]] = {}

        logger.info(
            f"[群防御] 插件已加载，阈值={self.threshold}，关键词='{self.report_keyword}'"
        )

    def _extract_target_from_message(self, event: AstrMessageEvent) -> str | None:
        """
        从消息中提取被举报的目标用户 ID (按顺序严格触发)
        """
        message_chain = event.message_obj.message
        if not message_chain:
            return None

        # keyword_seen 用于标记是否已经遇到了举报关键词
        keyword_seen = False
        
        for comp in message_chain:
            # 1. 遇到纯文本组件
            if isinstance(comp, Plain):
                if self.report_keyword in comp.text:
                    keyword_seen = True
                    # 检查文本自带数字的情况 (如："有内鬼 1234567")
                    after_keyword = comp.text[comp.text.find(self.report_keyword) + len(self.report_keyword):]
                    match_num = re.search(r"^\s*(\d+)", after_keyword)
                    if match_num:
                        return match_num.group(1)
            
            # 2. 遇到 At 组件
            elif isinstance(comp, At):
                # 只有在关键词出现之后碰到的 @ 才被视为有效举报目标
                # 这能完美解决 [引用] 或 @其他人 在前导致的误判
                if keyword_seen:
                    target_id = comp.qq
                    if target_id:
                        return str(target_id)
        
        # 3. 兜底正则匹配：应对某些适配器可能直接被序列化为字符串的情况
        message_raw = event.message_str.strip()
        keyword_idx = message_raw.find(self.report_keyword)
        if keyword_idx != -1:
            after_keyword = message_raw[keyword_idx + len(self.report_keyword):]
            match_at = re.search(r"\[At:(\d+)\]", after_keyword)
            if match_at:
                return match_at.group(1)

        return None

    async def _kick_group_member(
        self, event: AstrMessageEvent, group_id: str, user_id: str, reason: str = "多人举报"
    ) -> bool:
        """
        调用平台 API 踢出群成员
        """
        try:
            # 【修复】：获取精确的平台名称
            platform_name = event.get_platform_name()
            
            # 兼容多种 API 调用方式
            # 1. 如果 event.bot 带有 call_action（比如 aiocqhttp / napcat 适配器专属底层接口）
            if hasattr(event, "bot") and hasattr(event.bot, "call_action"):
                result = await event.bot.call_action(
                    "set_group_kick",
                    group_id=int(group_id),
                    user_id=int(user_id),
                    reject_add_request=False
                )
                logger.info(f"[群防御] 通过 call_action 踢出用户 {user_id}，返回: {result}")
                return True
                
            # 2. 如果 event.bot 没有 call_action，回退到原版的 plugin context 发送机制并补全参数
            platform = self.context.get_platform(platform_name)
            if not platform:
                logger.error(f"[群防御] 无法获取平台适配器: {platform_name}")
                return False

            payload = {
                "type": "set_group_kick",
                "group_id": int(group_id),
                "user_id": int(user_id),
                "reject_add_request": False,
            }
            
            if hasattr(platform, "send"):
                result = await platform.send(payload)
                if result and isinstance(result, dict) and result.get("status") == "ok":
                    logger.info(f"[群防御] 成功踢出用户 {user_id}，原因：{reason}")
                    return True
                else:
                    logger.warning(f"[群防御] 踢出用户 {user_id} 失败: {result}")
                    return False
            else:
                logger.error(f"[群防御] 当前平台 {platform_name} 不支持踢人 API 接口。")
                return False
                
        except Exception as e:
            logger.error(f"[群防御] 踢出用户时发生异常: {e}")
            return False

    @filter.command_group("defense")
    def defense_group(self):
        pass

    @defense_group.command("status")
    async def show_status(self, event: AstrMessageEvent):
        group_id = event.message_obj.group_id
        if not group_id:
            yield event.plain_result("该指令仅支持在群聊中使用。")
            return

        if not self.reports:
            yield event.plain_result("📊 当前没有举报记录。")
            return

        lines = ["📊 当前举报统计："]
        for target, reporters in self.reports.items():
            lines.append(f"• 目标 {target}: {len(reporters)} 人举报")
        yield event.plain_result("\n".join(lines))

    @defense_group.command("clear")
    async def clear_reports(self, event: AstrMessageEvent):
        sender_id = event.message_obj.sender.user_id
        logger.info(f"[群防御] 用户 {sender_id} 尝试清空举报记录")
        self.reports.clear()
        yield event.plain_result("✅ 已清空所有举报记录。")

    @filter.event_message_type(EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent):
        message_obj = event.message_obj
        group_id = message_obj.group_id
        sender_id = message_obj.sender.user_id
        message_str = event.message_str

        if not group_id:
            return

        # 检查纯文本内容是否以举报关键词开头，这里依然过滤无效消息
        if not message_str.strip().startswith(self.report_keyword):
            return

        target_id = self._extract_target_from_message(event)
        if not target_id:
            yield event.plain_result(
                f"❌ 请使用正确格式：{self.report_keyword} @用户 或 {self.report_keyword} QQ号"
            )
            return

        if target_id == sender_id:
            yield event.plain_result("❌ 你不能举报自己。")
            return

        if target_id not in self.reports:
            self.reports[target_id] = set()

        reporters = self.reports[target_id]

        if sender_id in reporters:
            yield event.plain_result("⚠️ 你已经举报过该用户。")
            return

        reporters.add(sender_id)
        current_count = len(reporters)

        logger.info(
            f"[群防御] 群 {group_id} 用户 {sender_id} 举报了 {target_id}，当前人数 {current_count}"
        )

        yield event.plain_result(
            f"📢 用户 {sender_id} 举报了 {target_id}。当前举报人数：{current_count}/{self.threshold}"
        )

        if current_count >= self.threshold:
            logger.info(
                f"[群防御] 用户 {target_id} 达到阈值 {self.threshold}，正在踢出..."
            )

            # 【修复】：踢人方法需要传入当前的 event 用于获取 API Context
            success = await self._kick_group_member(
                event, group_id, target_id, "多人举报"
            )

            if success:
                self.reports.pop(target_id, None)
                yield event.plain_result(
                    f"🚫 用户 {target_id} 因被多人举报，已被移出群聊。"
                )
            else:
                yield event.plain_result(
                    f"❌ 踢出用户 {target_id} 失败，请检查机器人是否有管理员权限或 API 是否兼容。"
                )

    async def terminate(self):
        self.reports.clear()
        logger.info("[群防御] 插件已卸载，已清理所有举报记录")
