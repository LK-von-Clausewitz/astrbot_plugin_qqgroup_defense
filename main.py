import re
from typing import Dict, Set

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
# 移除未使用的导入
# from astrbot.api.message_components import At, Plain
from astrbot.core.star.filter.event_message_type import EventMessageType


class GroupDefensePlugin(Star):
    """
    QQ 群防御插件
    监听群消息中的举报指令，累计举报人数，达到阈值后自动踢出被举报用户
    """

    def __init__(self, context: Context, config: dict = None):
        """
        初始化插件

        Args:
            context: AstrBot 上下文对象，用于与核心交互
            config: 插件配置字典，包含 threshold 和 reportKeyword
        """
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
        从消息中提取被举报的目标用户 ID

        支持两种格式：
        1. @提及用户：通过消息链中的 At 组件提取
        2. 纯文本格式："有内鬼 123456789"

        Args:
            event: 消息事件对象

        Returns:
            目标用户 ID 字符串，解析失败返回 None
        """
        # 方式1：从消息链中提取 @提及 的用户
        message_chain = event.message_obj.message
        if message_chain:
            for comp in message_chain:
                # 兼容不同版本的 At 组件结构
                if comp.type == "at":
                    target_id = getattr(comp, "qq", None)
                    if target_id:
                        return str(target_id)

        # 方式2：纯文本格式 "有内鬼 123456789"
        message_str = event.message_str.strip()
        pattern = rf"{re.escape(self.report_keyword)}\s+(\d+)"
        match = re.search(pattern, message_str)
        if match:
            return match.group(1)

        return None

    async def _kick_group_member(
        self, group_id: str, user_id: str, reason: str = "多人举报"
    ) -> bool:
        """
        调用平台 API 踢出群成员
        """
        try:
            platform = self.context.get_platform()
            if not platform:
                logger.error("[群防御] 无法获取平台适配器")
                return False

            # 调用 OneBot v11 标准的 set_group_kick Action
            payload = {
                "type": "set_group_kick",
                "group_id": int(group_id),
                "user_id": int(user_id),
                "reject_add_request": False,
            }
            result = await platform.send(payload)

            if result and result.get("status") == "ok":
                logger.info(f"[群防御] 成功踢出用户 {user_id}，原因：{reason}")
                return True
            else:
                logger.warning(f"[群防御] 踢出用户 {user_id} 失败: {result}")
                return False
        except Exception as e:
            logger.error(f"[群防御] 踢出用户时发生异常: {e}")
            return False

    @filter.command_group("defense")
    def defense_group(self):
        """防御插件指令组"""
        pass

    @defense_group.command("status")
    async def show_status(self, event: AstrMessageEvent):
        """
        查看当前举报统计
        """
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
        """
        清空所有举报记录（仅管理员可用）
        """
        sender_id = event.message_obj.sender.user_id
        logger.info(f"[群防御] 用户 {sender_id} 尝试清空举报记录")
        self.reports.clear()
        yield event.plain_result("✅ 已清空所有举报记录。")

    # 修正1：使用 event_message_type 监听所有消息，替代已弃用的 on_message
    @filter.event_message_type(EventMessageType.ALL)
    async def handle_message(self, event: AstrMessageEvent):
        """
        监听群消息，处理举报指令
        """
        # 修正2：直接从 event.message_obj 获取所需信息
        message_obj = event.message_obj
        group_id = message_obj.group_id
        sender_id = message_obj.sender.user_id
        message_str = event.message_str

        # 检查是否为群聊消息
        if not group_id:
            return

        # 检查是否以举报关键词开头
        if not message_str.strip().startswith(self.report_keyword):
            return

        # 提取被举报的目标用户 ID
        target_id = self._extract_target_from_message(event)
        if not target_id:
            yield event.plain_result(
                f"❌ 请使用正确格式：{self.report_keyword} @用户"
            )
            return

        # 不能举报自己
        if target_id == sender_id:
            yield event.plain_result("❌ 你不能举报自己。")
            return

        # 初始化该目标的举报者集合
        if target_id not in self.reports:
            self.reports[target_id] = set()

        reporters = self.reports[target_id]

        # 检查是否重复举报
        if sender_id in reporters:
            yield event.plain_result("⚠️ 你已经举报过该用户。")
            return

        # 记录举报
        reporters.add(sender_id)
        current_count = len(reporters)

        logger.info(
            f"[群防御] 群 {group_id} 用户 {sender_id} 举报了 {target_id}，当前人数 {current_count}"
        )

        # 发送举报进度提示
        yield event.plain_result(
            f"📢 用户 {sender_id} 举报了 {target_id}。当前举报人数：{current_count}"
        )

        # 判断是否达到阈值
        if current_count >= self.threshold:
            logger.info(
                f"[群防御] 用户 {target_id} 达到阈值 {self.threshold}，正在踢出..."
            )

            # 执行踢人
            success = await self._kick_group_member(
                group_id, target_id, "多人举报"
            )

            if success:
                # 踢出成功后清理记录
                self.reports.pop(target_id, None)
                yield event.plain_result(
                    f"🚫 用户 {target_id} 因被多人举报，已被移出群聊。"
                )
            else:
                yield event.plain_result(
                    f"❌ 踢出用户 {target_id} 失败，请检查机器人是否有管理员权限。"
                )

    async def terminate(self):
        """
        插件卸载/停用时的清理操作
        """
        self.reports.clear()
        logger.info("[群防御] 插件已卸载，已清理所有举报记录")
