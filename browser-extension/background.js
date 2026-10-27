// A tab-only bridge. It never injects scripts, types into pages, or changes tab groups.
export async function handle(request, api = chrome) {
  const tabInfo = tab => ({id: tab.id, title: tab.title || "", url: tab.url || "",
    windowId: tab.windowId, index: tab.index, active: tab.active});
  if (request.method === "list_tabs") {
    return {tabs: (await api.tabs.query({})).map(tabInfo)};
  }
  if (request.method !== "activate_tab" || !Number.isInteger(request.tabId)) {
    throw new Error("Unsupported tab operation");
  }
  const before = await api.tabs.get(request.tabId);
  if (before.url !== request.expectedUrl || before.windowId !== request.expectedWindowId) {
    throw new Error("The tab changed since it was found; search again");
  }
  await api.tabs.update(before.id, {active: true});
  await api.windows.update(before.windowId, {focused: true});
  const selected = await api.tabs.get(before.id);
  if (!selected.active || selected.url !== request.expectedUrl || selected.windowId !== request.expectedWindowId) {
    throw new Error("The browser did not confirm the selected tab");
  }
  return tabInfo(selected);
}

if (typeof chrome !== "undefined" && chrome.runtime?.connectNative) {
  let port;
  function connect() {
    if (port) return;
    const channel = chrome.runtime.connectNative("com.auren.tabs");
    port = channel;
    channel.postMessage({event: "hello"});
    channel.onMessage.addListener(async request => {
      try {
        channel.postMessage({id: request.id, result: await handle(request)});
      } catch (error) {
        channel.postMessage({id: request.id, error: String(error.message || error)});
      }
    });
    channel.onDisconnect.addListener(() => {
      // Reading lastError acknowledges an expected disconnect while Auren is updating.
      void chrome.runtime.lastError;
      if (port === channel) port = undefined;
      chrome.alarms.create("reconnect", {delayInMinutes: 0.5});
    });
  }
  chrome.runtime.onInstalled.addListener(connect);
  chrome.runtime.onStartup.addListener(connect);
  chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === "reconnect") connect(); });
  connect();
}
