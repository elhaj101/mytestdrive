const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('myTestDrive', {
  readText: (relativePath) => ipcRenderer.invoke('asset:read', relativePath, 'utf8'),
  readBinary: (relativePath) => ipcRenderer.invoke('asset:read', relativePath)
});
