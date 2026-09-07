const { app, BrowserWindow, contextBridge, ipcMain } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');

const projectRoot = path.resolve(__dirname, '../..');

ipcMain.handle('asset:read', async (_event, relativePath, encoding = null) => {
  const assetPath = path.resolve(projectRoot, relativePath);
  if (!assetPath.startsWith(`${projectRoot}${path.sep}`)) {
    throw new Error('Asset path is outside the project');
  }
  const data = await fs.readFile(assetPath);
  return encoding === 'utf8' ? data.toString('utf8') : data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
});

function createWindow() {
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 680,
    backgroundColor: '#0d1112',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.cjs')
    }
  });
  window.loadFile(path.join(projectRoot, 'app/renderer/dist/index.html'));
}

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
