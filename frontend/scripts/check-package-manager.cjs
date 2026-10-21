// Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: Apache-2.0

const fs = require('node:fs')
const path = require('node:path')

const rootDir = path.resolve(__dirname, '../..')
const root = JSON.parse(fs.readFileSync(path.join(rootDir, 'package.json'), 'utf8'))
const frontend = JSON.parse(fs.readFileSync(path.join(rootDir, 'frontend/package.json'), 'utf8'))

if (
  typeof root.packageManager !== 'string' ||
  !root.packageManager.startsWith('pnpm@') ||
  root.packageManager !== frontend.packageManager
) {
  console.error(
    `packageManager must specify the same pnpm version in package.json and frontend/package.json ` +
      `(root: ${root.packageManager}, frontend: ${frontend.packageManager})`
  )
  process.exit(1)
}

console.log(`packageManager matches: ${root.packageManager}`)
