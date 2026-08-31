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
 * 兩句互相打架的話：「這一頁存不了你的金鑰」和「貼上金鑰就能問 AI」。
 *
 * 說清楚一件事，因為它是真的：把 API 金鑰貼進網頁，本身就是釣魚網站訓練人做的
 * 動作。這一頁的原始碼是公開的、沒有後端、而且金鑰只往他指定的網址送——但這三件
 * 事都要他自己相信，所以畫面上直說，而不是假裝沒有這個問題。
 *
 * **模型名稱一律去問供應商，絕不寫死。** 第一版寫死了幾個我沒有查證過的 id，使用
 * 者一試就是
 *
 *     The model `llama-3.3-70b-versatile` does not exist or you do not have access to it.
 *
 * 那些名字會改、會下架，而且各家帳號的權限也不一樣。同一件事系統自己早就做對了
 * （AI 輔助那一頁的模型是抓來的、抓不到才讓人手打），這裡照抄那個做法。
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
    ;[KEY, URL_, MODEL].forEach(function (name) {
      try {
        window.sessionStorage.removeItem(name)
      } catch (e) {
        /* 同上 */
      }
    })
  }

  /** 瀏覽器直連供應商，最常見的失敗是對方不允許跨網域。那不是金鑰的問題。 */
  function friendly(error) {
    return error.message === 'Failed to fetch'
      ? '連不上這家供應商（它可能不允許從瀏覽器直接呼叫）。這不是金鑰的問題——裝好之後在系統裡問一樣可以。'
      : error.message
  }

  function listModels(base, key) {
    return fetch(base.replace(/\/$/, '') + '/models', {
      headers: { authorization: 'Bearer ' + key },
    })
      .then(function (response) {
        return response.json().then(function (body) {
          if (!response.ok) {
            throw new Error((body && body.error && body.error.message) || '讀不到模型清單')
          }
          return (body.data || [])
            .map(function (m) {
              return m.id
            })
            .filter(Boolean)
            .sort()
        })
      })
      .catch(function (error) {
        throw new Error(friendly(error))
      })
  }

  function ask(question, context) {
    return fetch(get(URL_).replace(/\/$/, '') + '/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: 'Bearer ' + get(KEY) },
      body: JSON.stringify({
        model: get(MODEL),
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
          return (
            (body.choices && body.choices[0] && body.choices[0].message.content) || '（沒有回應）'
          )
        })
      })
      .catch(function (error) {
        throw new Error(friendly(error))
      })
  }

  // 只有網址，沒有模型：模型是問來的。
  var VENDORS = [
    ['OpenRouter', 'https://openrouter.ai/api/v1'],
    ['NVIDIA', 'https://integrate.api.nvidia.com/v1'],
    ['Groq', 'https://api.groq.com/openai/v1'],
    ['OpenAI', 'https://api.openai.com/v1'],
  ]

  /** 第一頁：收金鑰，然後去問那家有哪些模型。 */
  function mountKeyForm(body) {
    body.innerHTML =
      '<p>貼上金鑰，這份引導的第 2、3 步就能直接問它。' +
      '<strong>這不是系統的設定</strong>——那個要在第 3 步或裝完之後填。</p>' +
      '<p><select id="ai-vendor" class="assist-input"></select></p>' +
      '<p><input id="ai-key" class="assist-input" type="password" placeholder="貼上金鑰" autocomplete="off"></p>' +
      '<p><button id="ai-save" class="btn">讀取模型清單</button> ' +
      '<button id="ai-forget" class="btn ghost">清除</button></p>' +
      '<p id="ai-model-row" hidden><select id="ai-model" class="assist-input"></select></p>' +
      '<p id="ai-state" class="assist-state"></p>' +
      '<p class="gotcha">金鑰只存在這個瀏覽器分頁，關掉就沒了，而且只會送到你選的那家供應商——' +
      '這幾頁是 GitHub 上的靜態檔，沒有後端可以經過。' +
      '把 API 金鑰貼進網頁本來就是要小心的動作，所以這裡說清楚：不貼完全沒關係，' +
      '第 2、3 步照樣讀得完。</p>'

    var vendor = body.querySelector('#ai-vendor')
    VENDORS.forEach(function (v) {
      var option = document.createElement('option')
      option.value = v[1]
      option.textContent = v[0]
      vendor.appendChild(option)
    })
    if (get(URL_)) vendor.value = get(URL_)

    var state = body.querySelector('#ai-state')
    var row = body.querySelector('#ai-model-row')
    var models = body.querySelector('#ai-model')

    if (get(MODEL)) state.textContent = '已記住：' + get(MODEL)

    models.addEventListener('change', function () {
      set(MODEL, models.value)
      state.textContent = '好了。第 2、3 步會出現「問 AI」。'
    })

    body.querySelector('#ai-save').addEventListener('click', function () {
      var key = body.querySelector('#ai-key').value.trim()
      if (!key) {
        state.textContent = '還沒貼上金鑰。'
        return
      }
      state.textContent = '問問這家有哪些模型…'
      // 存網址和金鑰，但**還不能用**：沒有模型就不算設定好，所以 MODEL 先清掉。
      set(KEY, key)
      set(URL_, vendor.value)
      set(MODEL, '')
      body.querySelector('#ai-key').value = ''

      listModels(vendor.value, key).then(
        function (list) {
          if (!list.length) {
            state.textContent = '這家沒有回傳模型清單。裝好之後在系統裡設定一樣可以。'
            return
          }
          models.innerHTML = ''
          list.forEach(function (id) {
            var option = document.createElement('option')
            option.value = id
            option.textContent = id
            models.appendChild(option)
          })
          row.hidden = false
          // 免費的排前面，因為多數人是為了不花錢才走這條路。
          var free = list.filter(function (id) {
            return /:free$/.test(id)
          })
          models.value = free.length ? free[0] : list[0]
          set(MODEL, models.value)
          state.textContent = '選一個模型（已先幫你選' + (free.length ? '免費的' : '第一個') + '）。'
        },
        function (error) {
          state.textContent = error.message
        },
      )
    })

    body.querySelector('#ai-forget').addEventListener('click', function () {
      clearAll()
      row.hidden = true
      state.textContent = '已清除。'
    })
  }

  /** 第二、三頁：有金鑰**也有模型**才出現。按了會失敗的東西不要給。 */
  /**
   * 沒有金鑰時的退路：把問題整理好，複製走。
   *
   * 使用者：「移到最後，中間有疑問要問誰?」——AI 排在第一步不是因為它是功能，是
   * 因為卡住的時候要有人問。而原本這裡開頭就 `if (!get(KEY)) return`，於是**最需
   * 要幫忙的那個人看到的是一片空白**：還沒申請金鑰的、或者把分頁關過的（金鑰只
   * 存在 sessionStorage）。
   *
   * 這幾頁每一個 ai-ask 都帶著 data-context——他正在做什麼的完整描述。把它跟他的
   * 問題拼成一段話複製到剪貼簿，他貼到任何免費的 AI 網頁版就有答案。零註冊、零金
   * 鑰，而且對「決定不用 AI」的人一樣成立。
   *
   * 講得出貼到哪裡去，不只說「找一個 AI」——對這個使用者那等於沒說。
   */
  function mountCopyFallback(root, body, context) {
    root.hidden = false
    body.innerHTML =
      '<p class="gotcha">還沒填金鑰也沒關係。把問題複製走，貼到你平常用的 AI ' +
      '（<a href="https://chatgpt.com" target="_blank" rel="noreferrer noopener">ChatGPT</a>、' +
      '<a href="https://claude.ai" target="_blank" rel="noreferrer noopener">Claude</a>、' +
      '<a href="https://gemini.google.com" target="_blank" rel="noreferrer noopener">Gemini</a> ' +
      '的免費網頁版都可以）——它會連你現在在做什麼一起帶過去。</p>' +
      '<p><input id="ai-q-copy" class="assist-input" type="text" ' +
      'placeholder="例如：Neon 的連線字串在哪裡複製？"></p>' +
      '<p><button id="ai-copy" class="btn">複製問題</button> ' +
      '<span id="ai-copied" class="assist-answer"></span></p>'

    var said = body.querySelector('#ai-copied')
    body.querySelector('#ai-copy').addEventListener('click', function () {
      var question = body.querySelector('#ai-q-copy').value.trim()
      var text =
        '我正在照一份安裝說明架一套自架的股票提醒系統，卡住了。\n\n' +
        '我現在在做的事：' +
        context +
        '\n\n我的問題：' +
        (question || '（請看上面的情況，告訴我下一步該做什麼）')

      function done(ok) {
        said.textContent = ok ? '複製好了，貼到 AI 那邊問吧。' : '複製不了，請手動選取上面那一行。'
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
          function () {
            done(true)
          },
          function () {
            done(false)
          },
        )
        return
      }
      // 舊瀏覽器、或非 https 的來源：clipboard API 不在。
      var area = document.createElement('textarea')
      area.value = text
      document.body.appendChild(area)
      area.select()
      var ok = false
      try {
        ok = document.execCommand('copy')
      } catch (e) {
        ok = false
      }
      document.body.removeChild(area)
      done(ok)
    })
  }

  function mountAsk(root, body, context) {
    // 金鑰＋模型都有才給「直接問」：少了模型就送出，換來的是一個一定會失敗的請求
    // 和一句他看不懂的錯誤。沒有的話走複製那條路——**不可以什麼都不給**。
    if (!get(KEY) || !get(MODEL)) {
      mountCopyFallback(root, body, context)
      return
    }
    root.hidden = false
    body.innerHTML =
      '<p><input id="ai-q" class="assist-input" type="text" placeholder="例如：Neon 的連線字串在哪裡複製？"></p>' +
      '<p><button id="ai-go" class="btn">問</button></p>' +
      '<p id="ai-a" class="assist-answer"></p>'

    var answer = body.querySelector('#ai-a')
    body.querySelector('#ai-go').addEventListener('click', function () {
      var question = body.querySelector('#ai-q').value.trim()
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
    var body = document.getElementById('ai-key-body')
    if (body) mountKeyForm(body)

    var box = document.getElementById('ai-ask')
    var boxBody = document.getElementById('ai-ask-body')
    if (box && boxBody) mountAsk(box, boxBody, box.dataset.context || '')
  })
})()
