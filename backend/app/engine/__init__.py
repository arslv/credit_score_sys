from app.engine.department_codes import load_department_codes
from app.engine.duplicates import is_passport_reused_with_different_identity
from app.engine.features import STAGE_A_FEATURE_SET_VERSION, compute_age, compute_stage_a_features
from app.engine.passport import (
    check_department_code_region,
    compute_blank_year,
    is_blank_year_future,
    is_issue_date_blank_year_mismatch,
    is_issue_date_implausible,
    is_passport_expired,
    validate_passport_format,
)
from app.engine.pipeline import NoActiveRulesetError, run_stage_a
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
    "check_department_code_region",
    "compute_blank_year",
    "is_blank_year_future",
    "is_issue_date_blank_year_mismatch",
    "is_issue_date_implausible",
    "is_passport_expired",
    "validate_passport_format",
    "is_passport_reused_with_different_identity",
    "load_department_codes",
]
