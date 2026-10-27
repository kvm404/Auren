import assert from "node:assert/strict";
import {test} from "node:test";
import {handle} from "../browser-extension/background.js";

function browser() {
  const tab = {id: 17, title: "Amazon.in", url: "https://www.amazon.in/", windowId: 4, index: 2,
    active: false, groupId: 22};
  const calls = [];
  const api = {tabs: {
    query: async () => [tab],
    get: async id => { assert.equal(id, 17); return {...tab}; },
    update: async (id, change) => { calls.push(["activate", id, change]); tab.active = change.active; }
  }, windows: {update: async (id, change) => calls.push(["focus", id, change])}};
  return {tab, calls, api};
}

test("activation preserves tab position, group and URL and verifies the result", async () => {
  const {tab, calls, api} = browser();
  const result = await handle({method: "activate_tab", tabId: 17, expectedUrl: tab.url, expectedWindowId: 4}, api);
  assert.equal(result.active, true);
  assert.equal(tab.groupId, 22);
  assert.equal(tab.index, 2);
  assert.equal(tab.url, "https://www.amazon.in/");
  assert.deepEqual(calls, [["activate", 17, {active: true}], ["focus", 4, {focused: true}]]);
});

test("listing metadata does not perform any browser mutation", async () => {
  const {calls, api} = browser();
  const {tabs} = await handle({method: "list_tabs"}, api);
  assert.equal(tabs[0].id, 17);
  assert.deepEqual(calls, []);
});

for (const change of [{expectedUrl: "https://another.example/"}, {expectedWindowId: 9}]) {
  test("a stale tab selection is rejected before activation " + JSON.stringify(change), async () => {
    const {tab, calls, api} = browser();
    await assert.rejects(handle({method: "activate_tab", tabId: 17, expectedUrl: tab.url,
      expectedWindowId: 4, ...change}, api), /tab changed/);
    assert.deepEqual(calls, []);
  });
}

test("failed activation cannot produce a success response", async () => {
  const {tab, api} = browser();
  api.tabs.update = async () => {};
  await assert.rejects(handle({method: "activate_tab", tabId: 17, expectedUrl: tab.url,
    expectedWindowId: 4}, api), /did not confirm/);
});

test("the bridge does not accept arbitrary browser or page commands", async () => {
  await assert.rejects(handle({method: "execute_script"}, browser().api), /Unsupported/);
});
