"""
消息构建器

负责构建各种响应消息
"""

from typing import Dict, Any, List


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
                print(f"LLM生成推荐消息失败: {e}")
        
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
                print(f"LLM生成拒绝消息失败: {e}")
        
        # 默认消息
        return "\n机器人：好的，我理解您的选择。您可以选择其他时间段，或者我可以为您重新推荐其他技师。请问您还有其他需要吗？\n"
    
    def create_appointment_failure_message(self, technician_name: str) -> str:
        """创建预约失败消息"""
        if technician_name and technician_name != "未知":
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
    
    def create_missing_info_questions(self, missing_info: List[str]) -> str:
        """根据缺失信息创建询问"""
        questions = [self.missing_info_prompts.get(field, f"请补充{field}信息") for field in missing_info]
        return "\n" + " ".join(questions) + "\n"
    
    def create_unrelated_message(self) -> str:
        """创建无关请求的消息"""
        return "[REPLY][预约机器人]抱歉，我无法处理这个问题。我只能帮您处理推拿服务相关的预约。请问您需要预约服务吗？\n"
    
    def create_parse_error_message(self) -> str:
        """创建解析错误消息"""
        return "[REPLY][预约机器人]\n机器人：解析失败，请重试。\n"
    
    def create_save_failure_message(self) -> str:
        """创建保存失败消息"""
        return "\n机器人：抱歉，预约保存失败，请重试。\n"
