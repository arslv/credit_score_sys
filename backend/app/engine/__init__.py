from app.engine.clients import compute_client_history_features, find_or_create_client
from app.engine.department_codes import load_department_codes
from app.engine.duplicates import is_passport_reused_with_different_identity
from app.engine.external_checks import run_fns_and_fedresurs, submit_fssp
from app.engine.features import STAGE_A_FEATURE_SET_VERSION, compute_age, compute_stage_a_features
from app.engine.freeze import find_active_sanctions_freeze
from app.engine.fssp import classify_production, summarize_productions
from app.engine.goods import compute_goods_features, find_goods_category
from app.engine.limits import DealLimitsOutcome, evaluate_deal_limits
from app.engine.passport import (
    check_department_code_region,
    compute_blank_year,
    is_blank_year_future,
    is_issue_date_blank_year_mismatch,
    is_issue_date_implausible,
    is_passport_expired,
    validate_passport_format,
)
from app.engine.pipeline import NoActiveRulesetError, finalize_stage_a, run_stage_a
from app.engine.stage_b_pipeline import NoStageBSnapshotError, finalize_stage_b, run_stage_b
from app.engine.rules import (
    FinancialOutcome,
    RuleEvaluationError,
    StopOutcome,
    TriggeredRule,
    evaluate_financial,
    evaluate_hard_stops,
    evaluate_stage_b_pre_bki,
    get_field,
    resolve_matrix_overrides,
)
from app.engine.schema import (
    MatrixEntry,
    Operator,
    RuleDef,
    RulesetConfig,
    load_ruleset_from_yaml,
    parse_ruleset,
)
from app.engine.state_machine import ALLOWED_TRANSITIONS, InvalidTransitionError, transition
from app.engine.versions import (
    RulesetSyncResult,
    compute_config_hash,
    get_active_ruleset_version,
    load_config,
    save_ruleset_version,
    sync_ruleset_from_file,
)

__all__ = [
    "FinancialOutcome",
    "RuleEvaluationError",
    "StopOutcome",
    "TriggeredRule",
    "evaluate_financial",
    "evaluate_hard_stops",
    "evaluate_stage_b_pre_bki",
    "get_field",
    "resolve_matrix_overrides",
    "MatrixEntry",
    "Operator",
    "RuleDef",
    "RulesetConfig",
    "load_ruleset_from_yaml",
    "parse_ruleset",
    "get_active_ruleset_version",
    "load_config",
    "save_ruleset_version",
    "RulesetSyncResult",
    "compute_config_hash",
    "sync_ruleset_from_file",
    "STAGE_A_FEATURE_SET_VERSION",
    "compute_age",
    "compute_stage_a_features",
    "ALLOWED_TRANSITIONS",
    "InvalidTransitionError",
    "transition",
    "NoActiveRulesetError",
    "run_stage_a",
    "finalize_stage_a",
    "find_active_sanctions_freeze",
    "classify_production",
    "summarize_productions",
    "check_department_code_region",
    "compute_blank_year",
    "is_blank_year_future",
    "is_issue_date_blank_year_mismatch",
    "is_issue_date_implausible",
    "is_passport_expired",
    "validate_passport_format",
    "is_passport_reused_with_different_identity",
    "load_department_codes",
    "find_or_create_client",
    "compute_client_history_features",
    "run_fns_and_fedresurs",
    "submit_fssp",
    "compute_goods_features",
    "find_goods_category",
    "DealLimitsOutcome",
    "evaluate_deal_limits",
    "run_stage_b",
    "finalize_stage_b",
    "NoStageBSnapshotError",
]
