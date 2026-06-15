"""
UserBehaviorAgent 功能测试（简化版）

测试用户行为代理的核心功能：
1. 记录用户行为数据
2. 分析用户偏好和模式
3. 生成回访提醒消息
"""

import pytest
import asyncio
from agents.user_behavior_agent import UserBehaviorAgent
from datetime import datetime, timedelta


class TestUserBehaviorAgentCoreFeatures:
    """测试用户行为代理核心功能"""
    
    def test_should_record_user_behavior_correctly(self):
        """
        测试：应该正确记录用户行为
        
        用户进行预约、咨询等操作时，应该记录相关行为数据
        """
        agent = UserBehaviorAgent()
        
        # 模拟用户行为数据
        behavior_data = {
            "user_id": "test_user_001",
            "action": "appointment_request",
            "service_type": "按摩",
            "preferred_time": "下午",
            "gender_preference": "女",
            "timestamp": datetime.now()
        }
        
        # 记录行为
        try:
            result = agent.behavior_recorder.record_behavior(behavior_data)
            
            # 应该成功记录
            assert result is not None, "行为记录应该返回结果"
            
            # 验证记录的数据
            recorded_behavior = agent.behavior_recorder.get_recent_behavior("test_user_001", limit=1)
            assert len(recorded_behavior) > 0, "应该能检索到刚记录的行为"
            
            latest_behavior = recorded_behavior[0]
            assert latest_behavior["action"] == "appointment_request"
            assert latest_behavior["service_type"] == "按摩"
            
        except Exception as e:
            pytest.fail(f"记录用户行为时出错：{e}")
    
    def test_should_analyze_user_preferences_correctly(self):
        """
        测试：应该正确分析用户偏好
        
        基于历史行为数据，分析用户的服务偏好、时间偏好等
        """
        agent = UserBehaviorAgent()
        
        # 模拟多次行为记录
        user_id = "test_user_002"
        behaviors = [
            {"user_id": user_id, "action": "appointment", "service_type": "按摩", "preferred_time": "下午"},
            {"user_id": user_id, "action": "appointment", "service_type": "按摩", "preferred_time": "下午"},
            {"user_id": user_id, "action": "appointment", "service_type": "推拿", "preferred_time": "上午"},
            {"user_id": user_id, "action": "consultation", "topic": "按摩效果"},
        ]
        
        # 记录所有行为
        for behavior in behaviors:
            agent.behavior_recorder.record_behavior(behavior)
        
        try:
            # 分析偏好
            preferences = agent.pattern_analyzer.analyze_user_preferences(user_id)
            
            # 应该识别出偏好模式
            assert preferences is not None, "应该返回偏好分析结果"
            
            # 应该识别出按摩是最常选择的服务（这个测试可能会失败）
            preferred_service = preferences.get("preferred_service_type")
            assert preferred_service == "按摩", f"应该识别按摩为偏好服务，但得到：{preferred_service}"
            
            # 应该识别出下午是偏好时间
            preferred_time = preferences.get("preferred_time")
            assert preferred_time == "下午", f"应该识别下午为偏好时间，但得到：{preferred_time}"
            
        except Exception as e:
            pytest.fail(f"分析用户偏好时出错：{e}")
    
    def test_should_generate_personalized_recommendations(self):
        """
        测试：应该生成个性化推荐
        
        基于用户历史行为和偏好，推荐合适的服务和时间
        """
        agent = UserBehaviorAgent()
        
        user_id = "test_user_003"
        
        # 建立用户行为历史
        historical_behaviors = [
            {"user_id": user_id, "service_type": "深层按摩", "satisfaction_rating": 5},
            {"user_id": user_id, "service_type": "肩颈按摩", "satisfaction_rating": 4},
            {"user_id": user_id, "preferred_time": "周末", "completion_status": "completed"},
        ]
        
        for behavior in historical_behaviors:
            agent.behavior_recorder.record_behavior(behavior)
        
        try:
            # 生成推荐
            recommendations = agent.recommendation_generator.generate_recommendations(user_id)
            
            # 应该返回推荐结果
            assert recommendations is not None, "应该返回推荐结果"
            assert len(recommendations) > 0, "推荐列表不应该为空"
            
            # 推荐应该与用户历史偏好相关
            recommended_services = [rec.get("service_type", "") for rec in recommendations]
            massage_related = any("按摩" in service for service in recommended_services)
            assert massage_related, f"推荐应该包含按摩相关服务，但得到：{recommended_services}"
            
        except Exception as e:
            pytest.fail(f"生成个性化推荐时出错：{e}")
    
    def test_should_identify_behavior_patterns(self):
        """
        测试：应该识别行为模式
        
        用户的预约频率、取消率、偏好变化等模式
        """
        agent = UserBehaviorAgent()
        
        user_id = "test_user_004"
        
        # 模拟一段时间的行为数据
        base_time = datetime.now() - timedelta(days=30)
        patterns_data = []
        
        for i in range(10):
            patterns_data.append({
                "user_id": user_id,
                "action": "appointment_request",
                "timestamp": base_time + timedelta(days=i*3),
                "service_type": "按摩" if i % 2 == 0 else "推拿"
            })
        
        # 添加一些取消记录
        patterns_data.extend([
            {"user_id": user_id, "action": "appointment_cancel", "timestamp": base_time + timedelta(days=5)},
            {"user_id": user_id, "action": "appointment_cancel", "timestamp": base_time + timedelta(days=15)},
        ])
        
        # 记录数据
        for data in patterns_data:
            agent.behavior_recorder.record_behavior(data)
        
        try:
            # 分析模式
            patterns = agent.pattern_analyzer.identify_behavior_patterns(user_id)
            
            # 应该识别出模式
            assert patterns is not None, "应该返回模式分析结果"
            
            # 应该计算预约频率
            appointment_frequency = patterns.get("appointment_frequency")
            assert appointment_frequency is not None, "应该计算预约频率"
            
            # 应该计算取消率
            cancellation_rate = patterns.get("cancellation_rate")
            assert cancellation_rate is not None, "应该计算取消率"
            assert 0 <= cancellation_rate <= 1, f"取消率应该在0-1之间，但得到：{cancellation_rate}"
            
        except Exception as e:
            pytest.fail(f"识别行为模式时出错：{e}")
    
    def test_should_provide_behavior_insights(self):
        """
        测试：应该提供行为洞察
        
        基于用户行为数据，提供有价值的洞察信息
        """
        agent = UserBehaviorAgent()
        
        user_id = "test_user_005"
        
        # 创建有趣的行为数据用于洞察
        insight_data = [
            {"user_id": user_id, "action": "consultation", "topic": "按摩效果", "timestamp": datetime.now()},
            {"user_id": user_id, "action": "appointment", "service_type": "按摩", "timestamp": datetime.now()},
            {"user_id": user_id, "action": "feedback", "rating": 5, "comment": "很满意"},
        ]
        
        for data in insight_data:
            agent.behavior_recorder.record_behavior(data)
        
        try:
            # 生成洞察
            insights = agent.insight_provider.generate_insights(user_id)
            
            # 应该返回洞察信息
            assert insights is not None, "应该返回洞察信息"
            assert len(insights) > 0, "洞察信息不应该为空"
            
            # 洞察应该包含有意义的分析
            insight_text = str(insights)
            meaningful_terms = ["偏好", "模式", "建议", "趋势", "习惯"]
            has_meaningful_content = any(term in insight_text for term in meaningful_terms)
            assert has_meaningful_content, f"洞察应该包含有意义的分析：{insight_text[:200]}..."
            
        except Exception as e:
            pytest.fail(f"提供行为洞察时出错：{e}")


class TestUserBehaviorAgentDataManagement:
    """测试数据管理功能"""
    
    def test_should_manage_user_preferences_storage(self):
        """
        测试：应该正确管理用户偏好存储
        
        存储、更新、检索用户偏好设置
        """
        agent = UserBehaviorAgent()
        
        user_id = "test_user_006"
        
        # 设置用户偏好
        preferences = {
            "preferred_service_types": ["按摩", "推拿"],
            "preferred_times": ["下午", "晚上"],
            "gender_preference": "女",
            "communication_style": "详细"
        }
        
        try:
            # 存储偏好
            agent.preference_manager.save_preferences(user_id, preferences)
            
            # 检索偏好
            retrieved_prefs = agent.preference_manager.get_preferences(user_id)
            
            # 应该能正确检索
            assert retrieved_prefs is not None, "应该能检索到偏好设置"
            assert retrieved_prefs["gender_preference"] == "女"
            assert "按摩" in retrieved_prefs["preferred_service_types"]
            
            # 更新偏好
            updated_prefs = {"preferred_times": ["上午"]}
            agent.preference_manager.update_preferences(user_id, updated_prefs)
            
            # 验证更新
            final_prefs = agent.preference_manager.get_preferences(user_id)
            assert "上午" in final_prefs["preferred_times"]
            
        except Exception as e:
            pytest.fail(f"管理用户偏好时出错：{e}")
    
    def test_should_handle_data_aggregation(self):
        """
        测试：应该处理数据聚合
        
        聚合多个用户的行为数据，生成统计信息
        """
        agent = UserBehaviorAgent()
        
        # 创建多个用户的数据
        users_data = [
            {"user_id": "user_1", "service_type": "按摩", "satisfaction": 5},
            {"user_id": "user_2", "service_type": "按摩", "satisfaction": 4},
            {"user_id": "user_3", "service_type": "推拿", "satisfaction": 3},
            {"user_id": "user_1", "service_type": "按摩", "satisfaction": 5},
        ]
        
        for data in users_data:
            agent.behavior_recorder.record_behavior(data)
        
        try:
            # 聚合数据
            aggregated = agent.behavior_processor.aggregate_behavior_data(
                time_range="last_30_days",
                group_by="service_type"
            )
            
            # 应该返回聚合结果
            assert aggregated is not None, "应该返回聚合结果"
            
            # 应该包含按摩和推拿的统计
            service_stats = aggregated.get("service_type_stats", {})
            assert "按摩" in str(service_stats), "聚合结果应该包含按摩统计"
            assert "推拿" in str(service_stats), "聚合结果应该包含推拿统计"
            
        except Exception as e:
            pytest.fail(f"数据聚合时出错：{e}")
    
    def test_should_handle_privacy_and_data_cleanup(self):
        """
        测试：应该处理隐私和数据清理
        
        过期数据清理、敏感信息处理等
        """
        agent = UserBehaviorAgent()
        
        user_id = "test_user_007"
        
        # 创建包含敏感信息的数据
        sensitive_data = {
            "user_id": user_id,
            "phone_number": "13800138000",  # 敏感信息
            "real_name": "张三",  # 敏感信息
            "service_type": "按摩",
            "timestamp": datetime.now() - timedelta(days=400)  # 过期数据
        }
        
        agent.behavior_recorder.record_behavior(sensitive_data)
        
        try:
            # 测试数据清理
            cleaned_count = agent.behavior_processor.cleanup_old_data(
                older_than_days=365
            )
            
            # 应该清理过期数据
            assert cleaned_count is not None, "应该返回清理数量"
            
            # 测试敏感信息处理
            anonymized_data = agent.behavior_processor.anonymize_sensitive_data(user_id)
            
            # 敏感信息应该被处理
            assert "13800138000" not in str(anonymized_data), "手机号码应该被匿名化"
            assert "张三" not in str(anonymized_data), "真实姓名应该被匿名化"
            
        except Exception as e:
            pytest.fail(f"隐私数据处理时出错：{e}")


class TestUserBehaviorAgentEdgeCases:
    """测试边界情况"""
    
    def test_should_handle_new_user_with_no_history(self):
        """
        测试：应该处理没有历史记录的新用户
        
        新用户应该得到默认推荐，不应该出错
        """
        agent = UserBehaviorAgent()
        
        new_user_id = "brand_new_user"
        
        try:
            # 为新用户生成推荐
            recommendations = agent.recommendation_generator.generate_recommendations(new_user_id)
            
            # 应该返回默认推荐
            assert recommendations is not None, "新用户应该得到默认推荐"
            
            # 分析新用户偏好
            preferences = agent.pattern_analyzer.analyze_user_preferences(new_user_id)
            
            # 应该有默认偏好或空结果，不应该出错
            assert preferences is not None or preferences == {}, "新用户偏好分析不应该出错"
            
        except Exception as e:
            pytest.fail(f"处理新用户时出错：{e}")
    
    def test_should_handle_invalid_behavior_data(self):
        """
        测试：应该处理无效的行为数据
        
        缺少必要字段、错误的数据类型等
        """
        agent = UserBehaviorAgent()
        
        invalid_data_cases = [
            {},  # 空数据
            {"user_id": None},  # 空用户ID
            {"user_id": "test", "timestamp": "invalid_date"},  # 无效时间
            {"user_id": "", "action": ""},  # 空字符串
        ]
        
        for invalid_data in invalid_data_cases:
            try:
                result = agent.behavior_recorder.record_behavior(invalid_data)
                
                # 如果没有抛出异常，应该有某种错误处理
                if result is not None:
                    # 验证错误处理机制
                    pass
                    
            except Exception as e:
                # 如果抛出异常，应该是可预期的类型
                assert isinstance(e, (ValueError, TypeError, KeyError)), \
                    f"无效数据异常类型错误：{type(e)}，数据：{invalid_data}"
