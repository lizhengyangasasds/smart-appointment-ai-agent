"""
消息构建器

负责构建各种响应消息
"""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class MessageBuilder:
    """消息构建器"""
    
    def __init__(self):
        self.missing_info_prompts = {
            "gender": "您希望选择男技师还是女技师呢？",
            "start_time": "请问您想预约的时间是？",
            "duration": "请问您需要多长时间的服务？",
            "project": "请问您需要什么服务项目？比如按摩？",
            "preference": "您对技师有力气大小等偏好吗？"
        }
    
    def create_appointment_success_message(self, tech: Dict[str, Any]) -> str:
        """创建预约成功消息"""
        # 检查是否是推荐技师
        if tech.get('is_recommendation'):
            original_tech = tech.get('original_technician', {})
            return (f"\n机器人：已为您预约技师：{tech['name']}，性别：{tech['gender']}。预约成功！"
                    f"（原指定的{original_tech.get('name', '')}技师时间冲突，{tech['name']}在相同服务方面同样专业）"
                    "今天下午北京最高温度39℃，出行请注意防晒，期待与您相遇\n")
        else:
            return (f"\n机器人：已为您预约技师：{tech['name']}，性别：{tech['gender']}。预约成功！"
                    "今天下午北京最高温度39℃，出行请注意防晒，期待与您相遇\n")

    def create_technician_recommendation_message(self, original_tech: Dict[str, Any], 
                                               recommended_tech: Dict[str, Any], 
                                               appointment_history: Dict[str, Any],
                                               llm=None) -> str:
        """创建技师推荐消息，使用LLM生成个性化措辞"""
        project = appointment_history.get('project', '按摩服务')
        start_time = appointment_history.get('start_time', '')
        
        if llm:
            try:
                # 构建LLM提示
                prompt = f"""
作为一个专业的预约助手，用户想预约{original_tech['name']}技师做{project}，但{original_tech['name']}技师在{start_time}这个时间段不空闲。

我找到了一位相似的技师：
- 姓名：{recommended_tech['name']}
- 性别：{recommended_tech['gender']}  
- 专长：{recommended_tech.get('strength', '')}

原技师专长：{original_tech.get('strength', '')}

请帮我生成一段温馨、专业的推荐话术，告诉用户原技师没空，但推荐技师在相同项目上同样专业，这个时间段有空，询问用户是否愿意预约推荐技师。

要求：
1. 语气温和、专业
2. 突出推荐技师的专业性
3. 明确询问用户意愿
4. 字数控制在80字以内
"""
                
                response = llm.invoke(prompt)
                if hasattr(response, 'content'):
                    generated_msg = response.content.strip()
                    if generated_msg:
                        return f"\n机器人：{generated_msg}\n"
                
            except Exception as e:
                logger.warning(f"LLM生成推荐消息失败: {e}")
        
        # 如果LLM失败，使用默认消息
        return (f"\n机器人：抱歉，{original_tech['name']}技师在{start_time}这个时间段不空闲。"
                f"不过{recommended_tech['name']}技师（{recommended_tech['gender']}）在{project}方面同样专业，"
                f"这个时间段有空，请问您愿意让我帮您预约{recommended_tech['name']}技师吗？\n")

    def create_recommendation_declined_message(self, llm=None) -> str:
        """创建用户拒绝推荐时的消息"""
        if llm:
            try:
                prompt = """
用户拒绝了我推荐的技师，请帮我生成一段专业、温馨的回复，表达理解并提供其他选择建议。

要求：
1. 表达理解用户的选择
2. 提供其他解决方案（如换时间、重新选择等）
3. 保持专业和友好的语气
4. 字数控制在60字以内
"""
                response = llm.invoke(prompt)
                if hasattr(response, 'content'):
                    generated_msg = response.content.strip()
                    if generated_msg:
                        return f"\n机器人：{generated_msg}\n"
            except Exception as e:
                logger.warning(f"LLM生成拒绝消息失败: {e}")
        
        # 默认消息
        return "\n机器人：好的，我理解您的选择。您可以选择其他时间段，或者我可以为您重新推荐其他技师。请问您还有其他需要吗？\n"
    
    def create_appointment_failure_message(self, technician_name: str) -> str:
        """创建预约失败消息

        关键：先校验 technician_name 是否真的是技师姓名。
        如果是描述性偏好（如"按摩服务"），直接走通用"没找到合适技师"分支，
        避免向用户抛出"没有找到名为'按摩服务'的技师"这种语义矛盾的回复。
        """
        if technician_name and technician_name != "未知" and self._looks_like_real_name(technician_name):
            # 通过Services层访问数据库
            from services.appointment_service import AppointmentService
            appointment_service = AppointmentService()
            specific_tech = appointment_service.get_technician_by_name(technician_name)
            if specific_tech:
                return f"\n机器人：抱歉，{technician_name}技师在您选择的时间段不空闲。请选择其他时间，或者我可以为您推荐其他技师。\n"
            else:
                return f"\n机器人：抱歉，没有找到名为'{technician_name}'的技师。请确认技师姓名，或者我可以为您推荐其他技师。\n"
        else:
            return "\n机器人：抱歉，该时间段没有合适的技师空闲，请选择其他时间或调整偏好。\n"

    @staticmethod
    def _looks_like_real_name(name: str) -> bool:
        """判断 technician_name 是否像真实技师姓名。

        与 InputParser._looks_like_real_name 同样的规则（2~4 个汉字、
        不能含服务项目关键词等），保持全链路口径一致。
        之前只看 "technician_name != '未知'" 是不严的——"按摩服务"也能通过这一关。
        """
        if not name or not isinstance(name, str):
            return False
        cleaned = name.strip()
        if not (2 <= len(cleaned) <= 4):
            return False
        if not all('\u4e00' <= ch <= '\u9fff' for ch in cleaned):
            return False
        invalid_keywords = [
            "按摩", "推拿", "足疗", "spa", "理疗", "养生",
            "经络", "刮痧", "拔罐", "服务", "技师", "老师",
            "手劲", "力气", "手法", "经验", "丰富", "高级", "中级", "初级",
        ]
        for kw in invalid_keywords:
            if kw in cleaned:
                return False
        return True
    
    def create_missing_info_questions(self, missing_info: List[str]) -> str:
        """根据缺失信息创建询问"""
        questions = [self.missing_info_prompts.get(field, f"请补充{field}信息") for field in missing_info]
        return "\n" + " ".join(questions) + "\n"

    def create_unknown_service_clarification(self, unknown_service: str,
                                             clarification_hint: str = "") -> str:
        """当用户提到知识库外的服务项目时（如油压、火罐、艾灸），生成反问话术。

        Args:
            unknown_service: 用户提到的原词（油压、火罐、艾灸 等）
            clarification_hint: LLM 生成的提示文案（如果可用，优先使用）

        Returns:
            友好的反问消息
        """
        # 优先用 LLM 生成的 hint，没有时用模板
        if clarification_hint and clarification_hint.strip():
            return f"\n机器人：{clarification_hint.strip()}\n"

        # 模板兜底 —— 列出本系统支持的服务，引导用户选择
        supported_list = "经络、肩颈、足疗、推拿、按摩、全身、SPA"
        return (
            f"\n机器人：抱歉，目前我们暂不提供「{unknown_service}」服务。"
            f"本店主要项目有：{supported_list} 等。"
            f"请问您想预约其中哪一项呢？\n"
        )

    def create_similar_service_clarification(self, unknown_service: str,
                                             best_match: str,
                                             best_score: float,
                                             second_candidates: list = None) -> str:
        """基于知识库相似度的"降级反问"文案 —— 用户体验升级关键函数。

        当用户提到库外服务词（如"油压"）时，先用 embedding 相似度找最近的库内服务，
        如果分数够高就直接给出"您是想约 XX 吗？"的高匹配反问 —— 比"列举所有服务"更精准。
        分数不够高时给 top3 候选让用户挑。

        Args:
            unknown_service: 用户原词（油压、火罐 等）
            best_match: 相似度最高的库内服务（如 拔罐/足疗）
            best_score: 相似度分数（0~1）
            second_candidates: [(service, score), ...] 备选 top2/top3，给低分场景用

        Returns:
            反问消息（带具体推荐项 + 备选项）
        """
        score_pct = int(round(best_score * 100))
        if second_candidates:
            others = "、".join(s for s, _ in second_candidates[:3])
            return (
                f"\n机器人：抱歉，店里暂时没有「{unknown_service}」这个项目。"
                f"看起来您是想约「{best_match}」（相似度 {score_pct}%）吗？"
                f"如果不合适，也可以选择：{others}。请问您想预约哪一项呢？\n"
            )
        return (
            f"\n机器人：抱歉，店里暂时没有「{unknown_service}」这个项目。"
            f"看起来您是想约「{best_match}」（相似度 {score_pct}%）吗？\n"
        )
    
    def create_unrelated_message(self) -> str:
        """创建无关请求的消息"""
        return "[REPLY][预约机器人]抱歉，我无法处理这个问题。我只能帮您处理推拿服务相关的预约。请问您需要预约服务吗？\n"
    
    def create_parse_error_message(self) -> str:
        """创建解析错误消息"""
        return "[REPLY][预约机器人]\n机器人：解析失败，请重试。\n"
    
    def create_save_failure_message(self) -> str:
        """创建保存失败消息"""
        return "\n机器人：抱歉，预约保存失败，请重试。\n"
