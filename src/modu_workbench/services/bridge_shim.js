/* 墨读·工作台 ⇄ main 分支前端桥接注入脚本（须在 React 入口前执行）。
 * 关键：window.formatFlow 同步定义（React 启动即认为桌面能力可用），
 * 方法内部等待 QWebChannel 桥就绪后再执行，避免“桌面能力未加载”竞态。 */
(function () {
  var bridgeWaiters = [];
  var bridgePromise = null;

  function readyBridge() {
    if (!bridgePromise) {
      bridgePromise = new Promise(function (resolve) {
        bridgeWaiters.push(resolve);
      });
    }
    return bridgePromise;
  }

  var pending = {};
  var seq = 1;

  function call(method) {
    var args = Array.prototype.slice.call(arguments, 1);
    return readyBridge().then(function (bridge) {
      var id = "r" + seq++;
      return new Promise(function (resolve, reject) {
        pending[id] = { resolve: resolve, reject: reject };
        try {
          bridge.invoke(id, method, JSON.stringify(args));
        } catch (e) {
          reject(e);
        }
      });
    });
  }

  function attachBridge(bridge) {
    window.__bridge = bridge;
    bridgePromise = Promise.resolve(bridge);
    bridge.resultReady.connect(function (reqId, json) {
      var p = pending[reqId];
      if (!p) return;
      delete pending[reqId];
      var data = json ? JSON.parse(json) : null;
      if (data && typeof data === "object" && "error" in data && !("ok" in data)) {
        p.reject(new Error(data.error));
      } else {
        p.resolve(data);
      }
    });
    bridge.eventReady.connect(function (kind, payload) {
      if (kind === "job" && window.__onJobEvent) {
        try {
          window.__onJobEvent(JSON.parse(payload));
        } catch (e) { /* ignore */ }
      }
    });
    var waiters = bridgeWaiters;
    bridgeWaiters = [];
    waiters.forEach(function (resolve) { resolve(bridge); });
    window.dispatchEvent(new Event("formatflow-ready"));
  }

  function initChannel() {
    if (window.QWebChannel && window.qt && window.qt.webChannelTransport) {
      new QWebChannel(qt.webChannelTransport, function (channel) {
        attachBridge(channel.objects.bridge);
      });
    }
  }

  /* —— 同步暴露 —— */
  window.formatFlow = {
    version: "0.2.0",
    importPaths: function (paths) { return call("importPaths", paths); },
    pickFiles: function () { return call("pickFiles"); },
    pickFolders: function () { return call("pickFolders"); },
    pickOutputDirectory: function () { return call("pickOutputDirectory"); },
    getDefaultOutputDir: function () { return call("getDefaultOutputDir"); },
    openOutputDirectory: function (dir) { return call("openOutputDirectory", dir); },
    getEngineStatus: function () { return call("getEngineStatus"); },
    getPathForFile: function () { return ""; },
    onJobsEvent: function (cb) {
      window.__onJobEvent = cb;
      return function () { window.__onJobEvent = null; };
    },
    cancelJobs: function (batchId) {
      readyBridge().then(function (bridge) { bridge.cancelJobs(batchId); });
    },
    startJobs: function (batchId, actionId, files, outputDir) {
      return call("startJobs", batchId, actionId, files, outputDir);
    },
    readDoc: function (path) { return call("readDoc", path); },
    saveDoc: function (path, content) { return call("saveDoc", path, content); },
    saveDocAs: function (source, name, content) { return call("saveDocAs", source, name, content); },
    openMedia: function (path) { return call("openMedia", path); },
    mediaUrl: function (path) { return call("mediaUrl", path); }
  };

  if (window.QWebChannel) {
    initChannel();
  } else {
    document.addEventListener("DOMContentLoaded", function () {
      if (window.QWebChannel && !window.__bridge) initChannel();
    });
  }
})();