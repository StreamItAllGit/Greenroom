# @module greenroom.core.profiles_config
# @purpose Neutral public presets, macro schedules, weighted series pool profiles, and reviewer aliases for Greenroom.
# @inputs Preset identifiers, optional custom targets, and target calendar dates (@depends-on pydantic)
# @outputs MacroTargetProfile, MacroScheduleConfig, SeriesPoolProfile, BUILTIN_MACRO_PRESETS, resolve_macro_targets (@exports MacroTargetProfile, MacroScheduleConfig, SeriesPoolProfile, SeriesPoolsConfig, ReviewerAliasConfig, BUILTIN_MACRO_PRESETS, DEFAULT_SERIES_POOL_NAME, resolve_macro_targets, list_builtin_macro_presets)
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, model_validator

DEFAULT_SERIES_POOL_NAME = "Default Pool"
DEFAULT_REVIEWER_ALIASES: List[str] = ["primary", "critic"]


class MacroTargetProfile(BaseModel):
    profile_id: str = "standard_daily"
    label: str = "Standard Daily"
    calories_kcal: float = 2000.0
    protein_grams: float = 150.0
    carbs_grams: float = 200.0
    fat_grams: float = 65.0
    estimated_burn_kcal: float = 2300.0
    schedule_label: str = "Standard Daily Baseline"
    deficit_strategy: str = "Balanced ~300 kcal daily deficit against ~2,300 kcal maintenance baseline."
    notes: str = "Standard balanced daily nutrition baseline."

    @model_validator(mode="before")
    @classmethod
    def _accept_camel_and_legacy_keys(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        data = dict(raw)
        mapping = {
            "profileId": "profile_id",
            "caloriesKcal": "calories_kcal",
            "calories": "calories_kcal",
            "proteinGrams": "protein_grams",
            "protein_g": "protein_grams",
            "carbsGrams": "carbs_grams",
            "carbs_g": "carbs_grams",
            "fatGrams": "fat_grams",
            "fat_g": "fat_grams",
            "estimatedBurnKcal": "estimated_burn_kcal",
            "scheduleLabel": "schedule_label",
            "deficitStrategy": "deficit_strategy",
        }
        for src, dst in mapping.items():
            if src in data and dst not in data:
                data[dst] = data[src]
        return data

    def to_api_dict(self) -> Dict[str, Any]:
        return {
            "profileId": self.profile_id,
            "label": self.label,
            "caloriesKcal": float(self.calories_kcal),
            "proteinGrams": float(self.protein_grams),
            "carbsGrams": float(self.carbs_grams),
            "fatGrams": float(self.fat_grams),
            "estimatedBurnKcal": float(self.estimated_burn_kcal),
            "scheduleLabel": self.schedule_label,
            "deficitStrategy": self.deficit_strategy,
            "notes": self.notes,
        }


class MacroScheduleConfig(BaseModel):
    preset_id: str = "standard_daily"
    label: str = "Standard Daily"
    description: str = "Consistent 2,000 kcal / 150g protein everyday baseline."
    active_weekdays: List[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    active_day: MacroTargetProfile = Field(default_factory=MacroTargetProfile)
    rest_day: MacroTargetProfile = Field(default_factory=MacroTargetProfile)

    def select_for_date(self, target_dt: datetime) -> MacroTargetProfile:
        return self.active_day if target_dt.weekday() in self.active_weekdays else self.rest_day

    def to_summary_dict(self) -> Dict[str, Any]:
        return {
            "presetId": self.preset_id,
            "label": self.label,
            "description": self.description,
            "activeWeekdays": list(self.active_weekdays),
            "activeDay": self.active_day.to_api_dict(),
            "restDay": self.rest_day.to_api_dict(),
        }


BUILTIN_MACRO_PRESETS: Dict[str, MacroScheduleConfig] = {
    "standard_daily": MacroScheduleConfig(
        preset_id="standard_daily",
        label="Standard Daily",
        description="Balanced 2,000 kcal daily target with consistent protein and carb targets every day.",
        active_weekdays=[0, 1, 2, 3, 4, 5, 6],
        active_day=MacroTargetProfile(),
        rest_day=MacroTargetProfile(),
    ),
    "active_shift": MacroScheduleConfig(
        preset_id="active_shift",
        label="Active / Shift Worker",
        description="Higher fuel targets (2,650 kcal) on active workdays paired with lighter 1,950 kcal rest days.",
        active_weekdays=[0, 1, 2, 3, 4],
        active_day=MacroTargetProfile(
            profile_id="active_shift_day",
            label="Active Workday",
            calories_kcal=2650.0,
            protein_grams=170.0,
            carbs_grams=290.0,
            fat_grams=90.0,
            estimated_burn_kcal=3450.0,
            schedule_label="Active Workday Schedule",
            deficit_strategy="Holding an adaptive ~700-800 kcal deficit against ~3,450 kcal active workday burn.",
            notes="Active Workday target (~700-800 kcal deficit against high daily output).",
        ),
        rest_day=MacroTargetProfile(
            profile_id="active_shift_rest",
            label="Recovery Off-Day",
            calories_kcal=1950.0,
            protein_grams=155.0,
            carbs_grams=220.0,
            fat_grams=50.0,
            estimated_burn_kcal=2450.0,
            schedule_label="Recovery Off-Day",
            deficit_strategy="Holding a ~500 kcal deficit against ~2,450 kcal resting baseline burn.",
            notes="Recovery Off-Day (~500 kcal deficit against resting burn).",
        ),
    ),
    "fitness_cut": MacroScheduleConfig(
        preset_id="fitness_cut",
        label="Fitness / Cut",
        description="Structured fat-loss preset alternating 1,850 kcal high-protein cut days and 2,250 kcal weekend refeed days.",
        active_weekdays=[0, 1, 2, 3, 4],
        active_day=MacroTargetProfile(
            profile_id="fitness_cut_day",
            label="Cut Day",
            calories_kcal=1850.0,
            protein_grams=175.0,
            carbs_grams=165.0,
            fat_grams=55.0,
            estimated_burn_kcal=2400.0,
            schedule_label="Fitness Cut Day",
            deficit_strategy="Holding a ~550 kcal deficit with 175g high-protein retention.",
            notes="Fitness Cut Day (high-protein satiety focus).",
        ),
        rest_day=MacroTargetProfile(
            profile_id="fitness_refeed_day",
            label="Maintenance / Refeed Day",
            calories_kcal=2250.0,
            protein_grams=165.0,
            carbs_grams=250.0,
            fat_grams=65.0,
            estimated_burn_kcal=2400.0,
            schedule_label="Refeed / Recovery Day",
            deficit_strategy="Maintenance glycogen refeed supporting recovery and adherence.",
            notes="Weekend recovery refeed near maintenance calories.",
        ),
    ),
    "custom": MacroScheduleConfig(
        preset_id="custom",
        label="Custom",
        description="User-defined custom calorie and macronutrient targets.",
        active_weekdays=[0, 1, 2, 3, 4, 5, 6],
        active_day=MacroTargetProfile(profile_id="custom", label="Custom Targets"),
        rest_day=MacroTargetProfile(profile_id="custom", label="Custom Targets"),
    ),
}


class SeriesPoolProfile(BaseModel):
    pool_id: str = "default_pool"
    display_name: str = DEFAULT_SERIES_POOL_NAME
    is_enabled: bool = True
    initial_weight: float = 5.0
    pick_penalty: float = 3.0
    floor_weight: float = 1.0
    unpicked_bonus: float = 1.0

    @model_validator(mode="before")
    @classmethod
    def _normalize_aliases(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        data = dict(raw)
        if "enabled" in data and "is_enabled" not in data:
            data["is_enabled"] = bool(data["enabled"])
        if "isEnabled" in data and "is_enabled" not in data:
            data["is_enabled"] = bool(data["isEnabled"])
        if "name" in data and "display_name" not in data:
            data["display_name"] = str(data["name"])
        if "displayName" in data and "display_name" not in data:
            data["display_name"] = str(data["displayName"])
        return data


class SeriesPoolsConfig(BaseModel):
    default_pool_name: str = DEFAULT_SERIES_POOL_NAME
    default: SeriesPoolProfile = Field(default_factory=SeriesPoolProfile)
    custom_pools: Dict[str, SeriesPoolProfile] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _shim_legacy_pool_keys(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        data = dict(raw)
        legacy_key = "rare"
        if legacy_key in data:
            legacy_val = data.get(legacy_key)
            if isinstance(legacy_val, dict):
                if "default" not in data:
                    data["default"] = legacy_val
                custom = dict(data.get("custom_pools") or {})
                custom.setdefault("legacy_pool", legacy_val)
                data["custom_pools"] = custom
            data.pop(legacy_key, None)
        return data

    def get_rule(self, pool_name: Optional[str] = None) -> SeriesPoolProfile:
        key = (pool_name or "").strip().lower()
        if key and key in self.custom_pools:
            return self.custom_pools[key]
        return self.default


class ReviewerAliasConfig(BaseModel):
    aliases: List[str] = Field(default_factory=lambda: list(DEFAULT_REVIEWER_ALIASES))
    primary_reviewer: str = "primary"


def list_builtin_macro_presets() -> List[Dict[str, Any]]:
    """Return UI-friendly metadata for all built-in public macro presets."""
    return [preset.to_summary_dict() for preset in BUILTIN_MACRO_PRESETS.values()]


def resolve_macro_targets(
    preset_id: str,
    target_dt: datetime,
    custom_targets: Optional[Dict[str, Any]] = None,
) -> MacroTargetProfile:
    """Resolve daily macro targets for target_dt from a built-in preset or user custom override."""
    norm_id = (preset_id or "standard_daily").strip().lower()
    if norm_id == "custom" and isinstance(custom_targets, dict):
        return MacroTargetProfile.model_validate(
            {
                "profile_id": "custom",
                "label": custom_targets.get("label") or "Custom Targets",
                "schedule_label": custom_targets.get("scheduleLabel") or "Custom Schedule",
                "calories_kcal": custom_targets.get("caloriesKcal", custom_targets.get("calories", 2000.0)),
                "protein_grams": custom_targets.get("proteinGrams", custom_targets.get("protein_g", 150.0)),
                "carbs_grams": custom_targets.get("carbsGrams", custom_targets.get("carbs_g", 200.0)),
                "fat_grams": custom_targets.get("fatGrams", custom_targets.get("fat_g", 65.0)),
                "notes": custom_targets.get("notes") or "Custom user macro targets.",
            }
        )
    preset = BUILTIN_MACRO_PRESETS.get(norm_id) or BUILTIN_MACRO_PRESETS["standard_daily"]
    return preset.select_for_date(target_dt)
