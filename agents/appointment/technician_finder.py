"""
技师查找器

负责根据用户需求查找合适的技师
"""

from typing import Optional, Dict, Any, Callable
from datetime import datetime, timedelta
from services.text_embedding import find_best_match_indices


class TechnicianFinder:
    """技师查找器"""
    
    def __init__(self):
        pass
    
    def parse_time_and_duration(self, start_time_str: str, duration_str: str) -> tuple:
        """解析预约时间和时长"""
        if not start_time_str or start_time_str == "未知":
            return None, None, None

        if not duration_str or duration_str == "未知":
            return None, None, None

        try:
            from config.time_config import time_config
            start_time = time_config.parse_datetime(start_time_str)
            if start_time is None:
                return None, None, None
            
            # 从字符串中提取数字作为时长（分钟）
            duration_min = int(''.join(filter(str.isdigit, str(duration_str))))
            if duration_min <= 0:
                return None, None, None

            end_time = start_time + timedelta(minutes=duration_min)
            return start_time, end_time, duration_min
        except Exception:
            return None, None, None
    
    def find_specific_technician(self, technician_name: str, start_time: datetime, 
                               end_time: datetime, yield_func: Optional[Callable] = None) -> Optional[Dict]:
        """查找指定技师的可用性"""
        # 通过Services层访问数据库
        from services.appointment_service import AppointmentService
        appointment_service = AppointmentService()
        
        if yield_func:
            yield_func(f"[THOUGHT][预约机器人] 用户指定了技师：{technician_name}，正在查询该技师信息...\n")
        
        specific_tech = appointment_service.get_technician_by_name(technician_name)
        if specific_tech:
            if yield_func:
                yield_func(f"[THOUGHT][预约机器人] 找到技师：{specific_tech['name']}，正在检查档期...\n")
            
            if appointment_service.is_technician_available(specific_tech["id"], start_time, end_time):
                if yield_func:
                    yield_func(f"[THOUGHT][预约机器人] {technician_name}技师在指定时间有空\n")
                return specific_tech
            else:
                if yield_func:
                    yield_func(f"[THOUGHT][预约机器人] {technician_name}技师在指定时间不空闲\n")
                return None
        else:
            if yield_func:
                yield_func(f"[THOUGHT][预约机器人] 未找到名为'{technician_name}'的技师\n")
            return None

    def find_similar_available_technician(self, target_technician: Dict[str, Any], 
                                        start_time: datetime, end_time: datetime, 
                                        yield_func: Optional[Callable] = None) -> Optional[Dict]:
        """根据目标技师的专长查找相似且可用的技师"""
        # 通过Services层访问数据库
        from services.appointment_service import AppointmentService
        appointment_service = AppointmentService()
        
        if yield_func:
            yield_func(f"[THOUGHT][预约机器人] 正在根据{target_technician['name']}的专长查找相似技师...\n")
        
        # 获取所有技师
        all_techs = appointment_service.get_all_technicians()
        if not all_techs:
            return None
            
        # 排除目标技师本身
        other_techs = [tech for tech in all_techs if tech['id'] != target_technician['id']]
        if not other_techs:
            return None
        
        # 获取目标技师的专长
        target_strength = target_technician.get('strength', '')
        if not target_strength:
            return None
            
        # 使用文本嵌入找到最相似的技师
        strengths = [tech.get('strength', '') for tech in other_techs]
        indices = find_best_match_indices(target_strength, strengths)
        
        if yield_func:
            yield_func(f"[THOUGHT][预约机器人] 根据专长相似度排序，准备检查可用性...\n")
        
        # 按相似度顺序检查技师可用性
        for index in indices:
            similar_tech = other_techs[index]
            if appointment_service.is_technician_available(similar_tech["id"], start_time, end_time):
                if yield_func:
                    yield_func(f"[THOUGHT][预约机器人] 找到相似且可用的技师：{similar_tech['name']}\n")
                return similar_tech
        
        if yield_func:
            yield_func(f"[THOUGHT][预约机器人] 没有找到相似且可用的技师\n")
        return None
    
    def filter_technicians_by_preference(self, all_techs: list, preference: str) -> list:
        """根据偏好筛选技师"""
        if not preference or preference == "无":
            return all_techs
        
        strengths = [tech.get("strength", "") for tech in all_techs]
        indices = find_best_match_indices(preference, strengths)
        return [all_techs[i] for i in indices]
    
    def filter_technicians_by_gender(self, all_techs: list, gender: str) -> list:
        """根据性别筛选技师"""
        if not gender or gender == "未知" or gender == "无":
            return all_techs
        
        # 标准化性别表示
        gender = gender.strip().lower()
        if gender in ["男", "男性", "男技师", "male"]:
            target_gender = "男"
        elif gender in ["女", "女性", "女技师", "female"]:
            target_gender = "女"
        else:
            return all_techs
        
        # 筛选匹配性别的技师
        filtered_techs = []
        for tech in all_techs:
            tech_gender = tech.get("gender", "").strip()
            if tech_gender == target_gender:
                filtered_techs.append(tech)
        
        return filtered_techs if filtered_techs else all_techs  # 如果没有匹配的，返回所有技师
    
    def find_available_technician(self, filtered_techs: list, all_techs: list, 
                                start_time: datetime, end_time: datetime, 
                                preference: str, gender: str = None, yield_func: Optional[Callable] = None) -> Optional[Dict]:
        """在技师列表中查找可用技师"""
        # 通过Services层访问数据库
        from services.appointment_service import AppointmentService
        appointment_service = AppointmentService()
        
        if yield_func:
            yield_func("[THOUGHT][预约机器人] 正在查找空闲技师...\n")
        
        # 先在筛选后的技师中查找
        for tech in filtered_techs:
            if appointment_service.is_technician_available(tech["id"], start_time, end_time):
                if yield_func:
                    yield_func(f"[THOUGHT][预约机器人] 找到空闲技师：{tech['name']}\n")
                return tech
        
        # 如果有偏好但没找到，再在所有技师中查找
        if preference and preference != "无" and filtered_techs != all_techs:
            if yield_func:
                yield_func("[THOUGHT][预约机器人] 偏好技师无空闲，尝试查找所有技师...\n")
            for tech in all_techs:
                if appointment_service.is_technician_available(tech["id"], start_time, end_time):
                    if yield_func:
                        yield_func(f"[THOUGHT][预约机器人] 找到空闲技师：{tech['name']}\n")
                    return tech
        
        if yield_func:
            yield_func("[THOUGHT][预约机器人] 没有找到空闲技师\n")
        return None
    
    def find_technician_with_thought(self, appointment_history: Dict[str, Any], 
                                   yield_func: Optional[Callable] = None) -> Optional[Dict]:
        """带思考提示的技师检索流程"""
        # 通过Services层访问数据库
        from services.appointment_service import AppointmentService
        appointment_service = AppointmentService()
        
        preference = appointment_history.get("preference")
        gender = appointment_history.get("gender")
        start_time_str = appointment_history.get("start_time")
        duration_str = appointment_history.get("duration")
        technician_name = appointment_history.get("technician_name")
        
        # 解析时间和时长
        start_time, end_time, duration_min = self.parse_time_and_duration(start_time_str, duration_str)
        if not start_time or not end_time:
            if yield_func:
                yield_func("[THOUGHT][预约机器人] 预约时间或时长信息不完整，无法检索技师\n")
            return None

        if yield_func:
            yield_func("[THOUGHT][预约机器人] 正在解析预约时间和时长...\n")

        # 优先处理指定技师
        if technician_name and technician_name != "未知":
            specific_tech = self.find_specific_technician(technician_name, start_time, end_time, yield_func)
            
            # 如果指定技师可用，直接返回
            if specific_tech:
                return specific_tech
            
            # 如果指定技师不可用，查找相似技师并返回推荐信息
            target_tech = appointment_service.get_technician_by_name(technician_name)
            if target_tech:
                similar_tech = self.find_similar_available_technician(target_tech, start_time, end_time, yield_func)
                if similar_tech:
                    # 返回包含推荐信息的结果，但标记为需要用户确认
                    return {
                        'is_recommendation': True,
                        'original_technician': target_tech,
                        'recommended_technician': similar_tech,
                        'requires_confirmation': True
                    }
            
            # 如果没有找到目标技师或相似技师，返回None
            return None

        # 通用查询逻辑
        if yield_func:
            yield_func("[THOUGHT][预约机器人] 正在检索所有技师数据...\n")
        
        all_techs = appointment_service.get_all_technicians()
        if not all_techs:
            if yield_func:
                yield_func("[THOUGHT][预约机器人] 没有找到任何技师数据\n")
            return None

        # 先根据性别筛选技师
        gender_filtered_techs = self.filter_technicians_by_gender(all_techs, gender)
        if yield_func and gender and gender != "未知":
            yield_func(f"[THOUGHT][预约机器人] 根据性别'{gender}'筛选技师，找到{len(gender_filtered_techs)}位技师\n")

        # 再根据偏好筛选技师
        filtered_techs = self.filter_technicians_by_preference(gender_filtered_techs, preference)
        if yield_func and preference and preference != "无":
            yield_func(f"[THOUGHT][预约机器人] 根据偏好'{preference}'进一步筛选，找到{len(filtered_techs)}位技师\n")
        
        # 查找可用技师
        return self.find_available_technician(filtered_techs, gender_filtered_techs, start_time, end_time, preference, gender, yield_func)
