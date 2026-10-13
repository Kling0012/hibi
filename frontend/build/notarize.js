const { execFileSync } = require('child_process')
const { notarize } = require('@electron/notarize')

const BUNDLE_ID = 'com.kling0012.hibi'

exports.default = async function notarizing(context) {
  if (context.electronPlatformName !== 'darwin') {
    return
  }

  const appName = context.packager.appInfo.productFilename
  const appPath = `${context.appOutDir}/${appName}.app`

  if (!process.env.APPLE_ID || !process.env.APPLE_APP_SPECIFIC_PASSWORD || !process.env.APPLE_TEAM_ID) {
    // Unsigned build: the bundle only carries Electron's linker signature
    // (identifier "Electron"), so macOS cannot tie privacy permissions
    // (Accessibility / Screen Recording) to Hibi and keeps asking. Give it a
    // proper ad-hoc signature with our bundle identifier instead.
    try {
      execFileSync('codesign', ['--force', '--deep', '--sign', '-', '--identifier', BUNDLE_ID, appPath], {
        stdio: 'inherit'
      })
      execFileSync('codesign', ['--verify', '--deep', '--strict', appPath], { stdio: 'inherit' })
      console.log('Ad-hoc signed app:', appPath)
    } catch (e) {
      console.warn('Ad-hoc signing failed (app stays linker-signed):', e)
    }
    return
  }

  console.log('Notarizing app:', appPath)

  await notarize({
    appPath,
    appBundleId: BUNDLE_ID,
    appleId: process.env.APPLE_ID,
    appleIdPassword: process.env.APPLE_APP_SPECIFIC_PASSWORD,
    teamId: process.env.APPLE_TEAM_ID
  })

  console.log('Notarized app:', appPath)
}
