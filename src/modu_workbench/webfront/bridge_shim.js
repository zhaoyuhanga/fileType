/* 墨读·工作台 ⇄ main 分支前端桥接注入脚本（须在 React 入口前执行）。 */
(function () {
  function defineBridge() {
    if (window.formatFlow) return;
    var pending = {};
    var seq = 1;
    var bridge = window.__bridge;
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
        try { window.__onJobEvent(JSON.parse(payload)); } catch (e) { /* ignore */ }
      }
    });
    function call(method) {
      var args = Array.prototype.slice.call(arguments, 1);
      var id = "r" + seq++;
      return new Promise(function (resolve, reject) {
        pending[id] = { resolve: resolve, reject: reject };
        try { bridge.invoke(id, method, JSON.stringify(args)); }
        catch (e) { reject(e); }
      });
    }
    window.formatFlow = {
      version: "0.2.0",
      importPaths: function (paths) { return call("importPaths", paths); },
      pickFiles: function () { return call("pickFiles"); },
      pickFolders: function () { return call("pickFolders"); },
      pickOutputDirectory: function () { return call("pickOutputDirectory"); },
      getDefaultOutputDir: function () { return call("getDefaultOutputDir"); },
      openOutputDirectory: function (dir) { return call("openOutputDirectory", dir); },
      getEngineStatus: function () { return call("getEngineStatus"); },
      getPathForFile: function () { return ""; }, // WebView 内不支持拖放取路径，请用按钮导入
      onJobsEvent: function (cb) {
        window.__onJobEvent = cb;
        return function () { window.__onJobEvent = null; };
      },
      cancelJobs: function (batchId) { bridge.cancelJobs(batchId); },
      startJobs: function (batchId, actionId, files, outputDir) {
        return call("startJobs", batchId, actionId, files, outputDir);
      },
      readDoc: function (path) { return call("readDoc", path); },
      saveDoc: function (path, content) { return call("saveDoc", path, content); },
      saveDocAs: function (source, name, content) { return call("saveDocAs", source, name, content); },
      openMedia: function (path) { return call("openMedia", path); }
      mediaUrl: function (path) { return call("mediaUrl", path); }
    };
    window.dispatchEvent(new Event("formatflow-ready"));
  }
  if (window.QWebChannel) {
    new QWebChannel(qt.webChannelTransport, function (channel) {
      window.__bridge = channel.objects.bridge;
      defineBridge();
    });
  } else {
    document.addEventListener("DOMContentLoaded", function () {
      if (window.QWebChannel && !window.__bridge) {
        new QWebChannel(qt.webChannelTransport, function (channel) {
          window.__bridge = channel.objects.bridge;
          defineBridge();
        });
      }
    });
  }
})();
