#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.application.services.marketplace.nutrition_service import NutritionService
from app.application.services.marketplace.utils import (
    calculate_servings,
    is_legal_video_url,
    parse_net_content,
)


class TestVideoWhitelist:
    def test_allows_youtube(self):
        assert is_legal_video_url("https://www.youtube.com/watch?v=abc")

    def test_allows_bilibili(self):
        assert is_legal_video_url("https://www.bilibili.com/video/BV1xx")

    def test_blocks_pan_links(self):
        assert not is_legal_video_url("https://pan.baidu.com/s/abc123")

    def test_blocks_unknown_domain(self):
        assert not is_legal_video_url("https://example-pirate.com/watch/123")


class TestNetContentParsing:
    def test_parse_chinese_label(self):
        result = parse_net_content("净含量：1000g")
        assert result is not None
        assert result["grams"] == 1000.0

    def test_parse_english_label(self):
        result = parse_net_content("Net Wt. 500 g")
        assert result is not None
        assert result["grams"] == 500.0

    def test_parse_kg(self):
        result = parse_net_content("净重 1.5kg")
        assert result is not None
        assert result["grams"] == 1500.0

    def test_parse_failure(self):
        assert parse_net_content("no content here") is None


class TestServingsCalculation:
    def test_calculate_servings(self):
        result = calculate_servings(1000, 50)
        assert result["servings"] == 20.0
        assert result["full_servings"] == 20

    def test_invalid_serving(self):
        with pytest.raises(ValueError):
            calculate_servings(1000, 0)


class TestNutritionAssessment:
    def setup_method(self):
        self.service = NutritionService()

    def test_high_calories_red_light(self):
        assessment = self.service._assess(
            {"calories": 750, "protein": 35, "fat": 20, "carbs": 60},
            weight_kg=70,
            goal="cut",
        )
        assert assessment["lights"]["calories"] == "red"

    def test_low_protein_red_for_bulk(self):
        assessment = self.service._assess(
            {"calories": 400, "protein": 20, "fat": 10, "carbs": 40},
            weight_kg=80,
            goal="bulk",
        )
        assert assessment["lights"]["protein"] in {"red", "yellow"}

    def test_balanced_meal_green(self):
        assessment = self.service._assess(
            {"calories": 450, "protein": 35, "fat": 12, "carbs": 45},
            weight_kg=None,
            goal=None,
        )
        assert assessment["overall"] == "green"
