from app.domain.models.app_config import FeatureFlagsConfig


def test_ops_patrol_feature_flag_defaults_off() -> None:
    assert FeatureFlagsConfig().enable_ops_patrol is False


def test_ops_patrol_feature_flag_can_be_enabled_explicitly() -> None:
    assert FeatureFlagsConfig(enable_ops_patrol=True).enable_ops_patrol is True
