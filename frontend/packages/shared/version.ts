// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

/**
 * A version is a pre-release when it contains a hyphen
 * (e.g. 0.1.0-dev.3, 1.2.3-beta.1). Stable versions like 0.1.0 are not.
 */
export function isPrereleaseVersion(version: string): boolean {
  return version.includes('-')
}
