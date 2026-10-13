#!/usr/bin/env python
# -*- coding: utf-8 -*-

# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""
Component initialization utilities for OpenContext.
Separated from main OpenContext class for better maintainability.
"""

import importlib
from typing import Any, Dict, Optional

from opencontext.config.config_manager import ConfigManager
from opencontext.config.global_config import GlobalConfig
from opencontext.config.prompt_manager import PromptManager
from opencontext.managers.capture_manager import ContextCaptureManager
from opencontext.managers.consumption_manager import ConsumptionManager
from opencontext.storage.global_storage import get_storage
from opencontext.storage.unified_storage import UnifiedStorage
from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Import consumption components
from opencontext.context_consumption.generation.smart_tip_generator import SmartTipGenerator
from opencontext.context_consumption.generation.smart_todo_manager import SmartTodoManager

# Component mappings
# Screen capture is driven by the Electron frontend (frames streamed to
# /api/live/frame); the backend no longer ships built-in capture or VLM
# screenshot/document processors. Custom components can still be registered
# dynamically via their config "module"/"class" entries.
CAPTURE_COMPONENTS = {}

CONSUMPTION_COMPONENTS = {
    "smart_tip_generator": SmartTipGenerator,
    "smart_todo_manager": SmartTodoManager,
}


class ComponentInitializer:
    """Handles initialization of various OpenContext components."""

    def __init__(self):
        # Use global config
        global_config = GlobalConfig.get_instance()
        self.config_manager = global_config.get_config_manager()

        self.config = (
            self.config_manager.get_config()
            if self.config_manager
            else GlobalConfig.get_instance().get_config()
        )
        self.global_config = GlobalConfig.get_instance()

    def _to_camel_case(self, name: str) -> str:
        """Convert snake_case to CamelCase."""
        return "".join(word.capitalize() for word in name.split("_"))

    def initialize_capture_components(self, capture_manager: ContextCaptureManager) -> None:
        """Initialize context capture components."""
        capture_config = self.config.get("capture", {})
        if not capture_config or not capture_config.get("enabled", False):
            logger.info("Capture modules not found or not enabled in configuration")
            return

        for name, config in capture_config.items():
            if (
                name == "enabled"
                or not isinstance(config, dict)
                or not config.get("enabled", False)
            ):
                continue

            try:
                component_instance = self._create_capture_component(name, config)
                capture_manager.register_component(name, component_instance)
                capture_manager.initialize_component(name, config)
                logger.info(f"Capture component '{name}' initialized successfully")

            except Exception as e:
                # Fail-soft: a missing/unknown capture component (e.g. a removed
                # feature still listed in an old config file) only disables that
                # component; it must not stop the whole backend.
                logger.error(f"Failed to initialize capture component '{name}': {e}")

        logger.info("Context capture modules initialization complete")

    def _create_capture_component(self, name: str, config: Dict[str, Any]):
        """Create a capture component instance."""
        if name in CAPTURE_COMPONENTS:
            component_class = CAPTURE_COMPONENTS[name]
            return component_class()

        # Fallback to dynamic import
        module_path = config.get("module")
        class_name = config.get("class")

        if not module_path or not class_name:
            module_path = f"opencontext.context_capture.{name}"
            class_name = f"{self._to_camel_case(name)}Capture"
            logger.info(
                f"Auto-inferred capture component '{name}' module='{module_path}' and class='{class_name}'"
            )

        module = importlib.import_module(module_path)
        component_class = getattr(module, class_name)
        return component_class()

    def initialize_processors(
        self, processor_manager=None, processed_context_callback=None
    ) -> None:
        """
        Initialize context processors.

        The VLM screenshot/document processors and the context merger were
        removed (screen analysis now happens in live_monitor via
        /api/live/frame). Kept as a no-op so old configs that still carry a
        ``processing`` section (or user settings that enable it) do not break
        startup.
        """
        logger.info(
            "Context processors are deprecated (screenshot analysis moved to "
            "live_monitor); nothing to initialize"
        )

    def initialize_consumption_components(self) -> ConsumptionManager:
        consumption_manager = ConsumptionManager()

        # Start scheduled tasks (individual tasks controlled by their enabled flags)
        content_generation_config = self.config.get("content_generation", {})
        consumption_manager.start_scheduled_tasks(content_generation_config)

        logger.info("Context consumption components initialization complete")
        return consumption_manager

    def _create_consumption_component(self, name: str, config: Dict[str, Any]):
        """Create a consumption component instance."""
        if name in CONSUMPTION_COMPONENTS:
            component_class = CONSUMPTION_COMPONENTS[name]
            return component_class()  # Now use parameterless constructor

        # Fallback to dynamic import
        module_path = config.get("module")
        class_name = config.get("class")

        if not module_path or not class_name:
            module_path = f"opencontext.context_consumption.{name}"
            class_name = f"{self._to_camel_case(name)}Consumer"
            logger.info(
                f"Auto-inferred consumption component '{name}' module='{module_path}' and class='{class_name}'"
            )

        module = importlib.import_module(module_path)
        component_class = getattr(module, class_name)
        return component_class()
