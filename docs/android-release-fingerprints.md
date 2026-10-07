# Confirmed Android signing fingerprints

User-supplied environment assignments; certificate role (Play/EAS) has not been specified.

Staging package: com.zionking.ziona.staging
SHA-1: 86:F4:0E:8C:00:AC:6E:08:FD:3F:C1:3D:A3:F1:99:51:05:FB:BE:5E
SHA-256: B6:E0:F5:F2:C4:CC:04:44:13:67:46:2A:73:5E:4B:70:58:0E:A0:CB:FE:11:30:5A:E7:EF:52:28:84:9A:F6:51

Production package: com.zionking.ziona
SHA-1: 39:E1:0B:E6:22:08:6B:88:90:13:14:92:17:56:C3:F5:3C:69:36:0D
SHA-256: ED:9D:BD:54:63:28:CC:7A:AE:44:F9:59:04:AA:67:FC:56:0C:76:2C:18:69:BA:15:3A:0E:3F:35:59:F8:39:30

App Links use SHA-256 only. SHA-1 is recorded for separate Firebase/Google OAuth console configuration, which has not been changed.

Deploy the mobile public association JSON files to the matching web hosts and deploy the server changes to both API environments. Each host must serve /.well-known/assetlinks.json directly. Staging must set ANDROID_APP_PACKAGE_NAME=com.zionking.ziona.staging and its existing staging fingerprint list; production must set com.zionking.ziona. Confirmed certificates are appended to the configured list without removing existing certificates.
