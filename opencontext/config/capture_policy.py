# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Shared synchronization for capture-policy publication and send admission."""

import threading
import weakref

from opencontext.utils.logging_utils import get_logger

logger = get_logger(__name__)

# Short critical sections only: never held while waiting for network I/O.
capture_policy_lock = threading.RLock()
_capture_listeners = weakref.WeakSet()


def register_capture_listener(listener) -> None:
    with capture_policy_lock:
        _capture_listeners.add(listener)


def notify_capture_policy_changed() -> None:
    # The caller holds capture_policy_lock while publishing the new policy.
    for listener in list(_capture_listeners):
        try:
            listener.capture_policy_changed()
        except Exception as e:
            logger.warning(f"Failed to notify capture policy change: {e}")
