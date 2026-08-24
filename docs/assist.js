/**
 * 引導頁自己的 AI 助手。
 *
 * 使用者：「不是第一頁 API key 填完之後，第二、三頁的部分就可以導入 AI 輔助了
 * 嗎？但這個我看不出來。」——對，原本完全沒有。
 *
 * 這幾頁沒有後端（GitHub Pages 只會發靜態檔），所以做法是**瀏覽器直接打他自己選
 * 的那家供應商**。因此：
 *
 *   - 金鑰只存在 sessionStorage：關掉分頁就沒了，不會留在硬碟上。
 *   - 金鑰只送到他自己填的那個網址，不經過任何第三方——這幾頁沒有伺服器可以經過。
 *   - 不預設幫他記住。要記住是他按下「記住」才發生的事。
 *
 * 而且畫面要講清楚**這裡貼過不等於系統設定好了**。第一版沒講，於是同一頁上出現
 * 兩句互相打架的話：「這一頁存不了你的金鑰」和「貼上金鑰就能問 AI」。使用者直接
 * 問：「你這邊怎麼有辦法第一頁就輸入 API key 啟動第二三頁的說明？」——因為那是兩
 * 件事，而我把它們寫成同一件。
 *
 * 說清楚一件事，因為它是真的：把 API 金鑰貼進網頁，本身就是釣魚網站訓練人做的
 * 動作。這一頁的原始碼是公開的、沒有後端、而且金鑰只往他指定的網址送——但這三件
 * 事都要他自己相信，所以畫面上直說，而不是假裝沒有這個問題。
 */

(function () {
  'use strict'

  var KEY = 'guide.ai.key'
  var URL_ = 'guide.ai.url'
  var MODEL = 'guide.ai.model'

  function get(name) {
    try {
      return window.sessionStorage.getItem(name) || ''
    } catch (e) {
      return ''
    }
  }

  function set(name, value) {
    try {
      window.sessionStorage.setItem(name, value)
    } catch (e) {
      /* 無痕模式或封鎖儲存：那就這一頁有效，不是錯誤 */
    }
  }

  function clearAll() {
    try {
      window.sessionStorage.removeItem(KEY)
      window.sessionStorage.removeItem(URL_)
      window.sessionStorage.removeItem(MODEL)
    } catch (e) {
      /* 同上 */
    }
  }

  function ask(question, context) {
    var key = get(KEY)
    var base = (get(URL_) || 'https://openrouter.ai/api/v1').replace(/\/$/, '')
    var model = get(MODEL) || 'google/gemma-4-31b-it:free'
    return fetch(base + '/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: 'Bearer ' + key },
      body: JSON.stringify({
        model: model,
        messages: [
          {
            role: 'system',
            content:
              '你在協助一個不懂程式的人安裝一套自架的股票提醒系統。' +
              '用繁體中文、短句、具體步驟回答。不確定就說不確定，不要猜。',
          },
          { role: 'user', content: context + '\n\n我的問題：' + question },
        ],
      }),
    })
      .then(function (response) {
        return response.json().then(function (body) {
          if (!response.ok) {
            throw new Error((body && body.error && body.error.message) || '供應商回了錯誤')
          }
          return (body.choices && body.choices[0] && body.choices[0].message.content) || '（沒有回應）'
        })
      })
      .catch(function (error) {
        // 瀏覽器直接打供應商，最常見的失敗是對方不允許跨網域呼叫。那不是金鑰的
        // 問題，說出來免得他去重辦一把。
        throw new Error(
          error.message === 'Failed to fetch'
            ? '連不上這家供應商（可能是它不允許從瀏覽器直接呼叫）。裝好之後在系統裡問一樣可以。'
            : error.message,
        )
      })
  }

  /** 第一頁的金鑰輸入。 */
  function mountKeyForm(root) {
    var saved = get(KEY)
    root.innerHTML =
      '<div class="detail-body">' +
      '<p>貼上金鑰，這份引導的第 2、3 步就能直接問它。' +
      '<strong>這不是系統的設定</strong>——那個要在第 3 步或裝完之後填。</p>' +
      '<p><select id="ai-vendor" class="assist-input"></select></p>' +
      '<p><input id="ai-key" class="assist-input" type="password" placeholder="sk-…" autocomplete="off"></p>' +
      '<p><button id="ai-save" class="btn">記住（只在這個分頁）</button> ' +
      '<button id="ai-forget" class="btn ghost">清除</button></p>' +
      '<p id="ai-state" class="assist-state"></p>' +
      '<p class="gotcha">金鑰只存在這個瀏覽器分頁，關掉就沒了，而且只會送到你選的那家供應商——' +
      '這幾頁是 GitHub 上的靜態檔，沒有後端可以經過。' +
      '把 API 金鑰貼進網頁本來就是要小心的動作，所以這裡說清楚：不貼完全沒關係，' +
      '第 2、3 步照樣讀得完。</p>' +
      '</div>'

    var vendors = [
      ['https://openrouter.ai/api/v1', 'google/gemma-4-31b-it:free', 'OpenRouter'],
      ['https://integrate.api.nvidia.com/v1', 'meta/llama-3.3-70b-instruct', 'NVIDIA'],
      ['https://api.groq.com/openai/v1', 'llama-3.3-70b-versatile', 'Groq'],
      ['https://api.openai.com/v1', 'gpt-4o-mini', 'OpenAI'],
    ]
    var select = root.querySelector('#ai-vendor')
    vendors.forEach(function (v) {
      var option = document.createElement('option')
      option.value = v[0]
      option.dataset.model = v[1]
      option.textContent = v[2]
      select.appendChild(option)
    })
    if (get(URL_)) select.value = get(URL_)

    var state = root.querySelector('#ai-state')
    if (saved) state.textContent = '已記住一把金鑰。'

    root.querySelector('#ai-save').addEventListener('click', function () {
      var value = root.querySelector('#ai-key').value.trim()
      if (!value) {
        state.textContent = '還沒貼上金鑰。'
        return
      }
      set(KEY, value)
      set(URL_, select.value)
      set(MODEL, select.selectedOptions[0].dataset.model)
      root.querySelector('#ai-key').value = ''
      state.textContent = '記住了。第 2、3 步會出現「問 AI」。'
    })

    root.querySelector('#ai-forget').addEventListener('click', function () {
      clearAll()
      state.textContent = '已清除。'
    })
  }

  /** 第二、三頁的提問框。沒有金鑰就整塊不出現——按了會失敗的東西不要給。 */
  function mountAsk(root, context) {
    if (!get(KEY)) return
    root.hidden = false
    root.innerHTML =
      '<summary>問 AI（用你第 1 步存的金鑰）</summary>' +
      '<div class="detail-body">' +
      '<p><input id="ai-q" class="assist-input" type="text" placeholder="例如：Neon 的連線字串在哪裡複製？"></p>' +
      '<p><button id="ai-go" class="btn">問</button></p>' +
      '<p id="ai-a" class="assist-answer"></p>' +
      '</div>'

    var answer = root.querySelector('#ai-a')
    root.querySelector('#ai-go').addEventListener('click', function () {
      var question = root.querySelector('#ai-q').value.trim()
      if (!question) return
      answer.textContent = '問問看…'
      ask(question, context).then(
        function (reply) {
          answer.textContent = reply
        },
        function (error) {
          answer.textContent = error.message
        },
      )
    })
  }

  document.addEventListener('DOMContentLoaded', function () {
    var form = document.getElementById('ai-key-form')
    if (form) mountKeyForm(form)

    var box = document.getElementById('ai-ask')
    if (box) mountAsk(box, box.dataset.context || '')
  })
})()
