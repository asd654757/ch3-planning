"""冻结协议常量（2026-09-08）。实验脚本/统计以此为准。"""

# 实验 A 四臂
BASELINES = {
    "B0": "Direct（自由文本，无校验；独立生成 + 解析器）",
    "B1": "Structured（ModelPlan P 直接用）",
    "B2a": "+Validation（P -> Validator，不过 Safe Stop）",
    "B2b": "Ours（P -> Validator -> 一次前缀保留修正）",
}
# B1/B2a/B2b 共享同一次 VLM 初始 ModelPlan P；B0 单独生成。

# 修复对比三组
REPAIR_GROUPS = {
    "R0": "Retry from Scratch（无错误定位，整段重生成）",
    "R1": "Full-Plan Repair（错误定位+原因+状态，整段重生成）",
    "R2": "Ours（输入与 R1 相同，锁定合法前缀只生成 suffix）",
}

# 指标分组（冻结口径：不要"一锅算"）
INDICATORS = {
    "group1_model_plan_quality": ["FVR", "CVR", "EPR", "GSR"],   # B0/B1
    "group2_system_behavior": [                                  # B1/B2a/B2b
        "IDR", "CRR", "FRR", "Final_Task_Ready", "GSR_after_repair",
        "llm_calls", "latency",
    ],
}

# 错误码 E01..E08（与 ch3.validator.errors 符号一致）
ERROR_ID_PUBLIC = {
    "SCHEMA_ERROR": "E01",
    "UNKNOWN_OBJECT": "E02",
    "UNREGISTERED_SKILL": "E03",
    "MISSING_PARAMETER": "E04",
    "ARM_NOT_EMPTY": "E05",
    "OBJECT_NOT_HELD": "E06",
    "TARGET_NOT_FOUND": "E07",
    "STATE_TRANSITION_ERROR": "E08",
}
