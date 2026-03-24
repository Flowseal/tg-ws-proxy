# Add project specific ProGuard rules here.

-dontwarn java.awt.**
-dontwarn java.beans.**
-dontwarn javax.swing.**
-dontwarn com.sun.jna.**
# Keep JNA interfaces and methods from being removed or obfuscated
-keep class com.sun.jna.** { *; }
-keep interface com.sun.jna.Library { *; }

# Keep our proxy library interface and NativeProxy object
-keep class com.example.tgwsproxy.NativeProxy { *; }
-keep interface com.example.tgwsproxy.ProxyLibrary { *; }
-keepclassmembers class * extends com.sun.jna.Library {
    <methods>;
}
