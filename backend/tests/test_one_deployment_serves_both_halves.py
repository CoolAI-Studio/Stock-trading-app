"""後端直接供應前端，所以只要部署一次。

＊ 為什麼要這樣。

原本要部署兩次：後端（Render／Railway／Fly.io／自己的機器都行）加前端（只給了
Vercel）。那個不對稱有兩個後果：

  一、引導頁對後端給了三個選擇，對前端只給一個——而使用者早就問過同一句話：
      「render 只是其一的解法不是嗎？」
  二、更新的路徑被綁在 Vercel 上：sync-from-upstream.yml 只在「前端是一份 GitHub
      複製品而且 Actions 開著」的時候有用。

後端供應前端之後，每一條**後端**的路都自動涵蓋前端，而那些路本來就是通用的。

＊ 這不是拿掉 Vercel，是拿掉「必須再部署一次」。

想把前端另外放的人照樣可以：設 VITE_API_BASE_URL 指向他的後端就好。少掉的是**要
求**，不是選擇。

＊ 這一組守的是「不可以安靜地壞掉」的那幾條。

靜態檔掛在 `/` 上，而 `/` 會吃掉所有沒被前面的路由接走的路徑。掛錯順序的話：

    /api/... 被靜態檔吃掉  → 前端拿到 HTML，而它在等 JSON。錯誤訊息會是
                             「Unexpected token '<'」，跟真正的原因差了十萬八千里。
    dist 不存在就開不了機  → 而這個 app 的鐵律是警告不能停擺。開發環境、還沒建過
                             前端的映像檔，都不可以讓 API 起不來。
"""

import pytest


def test_the_api_is_not_shadowed_by_the_static_files(client):
    """**這一條最重要。**

    靜態檔掛在 `/`，而如果它排在 API 前面，`/api/...` 會拿到 index.html。前端收到
    HTML 而它在等 JSON，錯誤訊息是「Unexpected token '<'」——一個跟真正原因差了十
    萬八千里的訊息。
    """
    resp = client.get("/api/setup/status")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")


def test_an_unknown_api_path_is_a_json_404_not_the_app_shell(client):
    """不存在的 API 路徑要回 JSON 的 404，不是 index.html。

    回 HTML 的話，一個打錯的網址會讓前端以為請求成功了，然後在解析的時候炸掉。而
    這種錯誤最常發生在「後端更新了、前端還是舊的」的時候——正是這整套機制在處理的
    情況。
    """
    resp = client.get("/api/this-does-not-exist")

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/json")


def test_healthz_is_not_shadowed_either(client):
    """外部看門狗每五分鐘打它一次，而它是「這個部署還活著嗎」唯一的答案。"""
    resp = client.get("/healthz")

    assert resp.status_code in (200, 503)
    assert resp.headers["content-type"].startswith("application/json")


def test_the_app_starts_without_a_built_frontend(client):
    """**沒有 dist 也要起得來。**

    開發環境沒有建過前端；一個只建後端的映像檔也沒有。而這個 app 的鐵律是警告不能
    停擺——一個因為找不到靜態檔而起不來的 API，是把「畫面沒了」升級成「提醒沒了」。

    這一條在測試環境裡本來就成立（測試不會去建前端），所以它其實是在守「不要哪天
    有人把它改成必需的」。
    """
    resp = client.get("/healthz")

    assert resp.status_code in (200, 503)


def test_a_frontend_route_falls_back_to_the_app_shell(client, tmp_path, monkeypatch):
    """`/strategies` 這種前端路由要回 index.html。

    單頁應用的路由在瀏覽器裡，不在伺服器上。使用者按 F5 重新整理 `/strategies`，
    伺服器上沒有那個檔案——回 404 的話他看到的是一個壞掉的網站，而他什麼都沒做錯。
    """
    from app import main

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>app</title>", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)

    resp = client.get("/strategies")

    assert resp.status_code == 200
    assert "<!doctype html>" in resp.text.lower()


def test_a_real_static_file_is_served_as_itself(client, tmp_path, monkeypatch):
    """真的存在的檔案要照原樣送出去，不要被 SPA 的 fallback 吃掉。"""
    from app import main

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)

    resp = client.get("/assets/app.js")

    assert resp.status_code == 200
    assert "console.log(1)" in resp.text


@pytest.mark.parametrize("path", ["/api/nope", "/healthz", "/ws"])
def test_the_backend_owns_these_prefixes_forever(client, tmp_path, monkeypatch, path):
    """就算 dist 存在，這幾個前綴也永遠是後端的。

    列出來而不是靠「排在前面就好」：排序是一個沒有東西守著的約定，而它壞掉的方式
    是靜默的——前端會拿到 HTML 然後在解析時炸掉。
    """
    from app import main

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)

    resp = client.get(path)

    assert "text/html" not in resp.headers.get("content-type", "")


# --- 還沒設定完的那一份，畫面也要出得來 -------------------------------------
#
# 這一組是實地跑出來的：first-deploy 那個 job 用全空的設定跑真的容器，然後
# `GET / → 503`。以前前端在 Vercel，`/` 根本不會經過設定模式那個鎖；畫面搬進同一個
# 服務之後，那個鎖**把設定頁自己鎖在外面了**——而它就是為了解釋「你還缺什麼」而存在
# 的那一頁。
#
# 症狀對使用者是：按完 Deploy、拿到網址、打開，看到一行 JSON 寫著「這個部署還沒設定
# 完成」。他不是工程師，那一行對他等於流程結束。


@pytest.fixture
def blank_deployment(monkeypatch, tmp_path):
    """一份剛部署好、什麼都還沒填的實例，而且映像檔裡有建好的前端。"""
    from app import main

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<!doctype html><div id="root"></div>', encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)
    monkeypatch.setattr("app.main.SETUP_MODE_REASON", "JWT_SECRET is unset or still a placeholder")
    return dist


def test_a_blank_deployment_still_serves_the_page(client, blank_deployment):
    """**這一條是這次 first-deploy 紅掉的原因。**

    他手上只有一個網址。打開它如果是 503，那個要告訴他「還缺 JWT_SECRET，按這顆按鈕
    產生」的頁面就永遠到不了——而它是這個時間點上唯一有用的東西。
    """
    resp = client.get("/")

    assert resp.status_code == 200, resp.text
    assert "text/html" in resp.headers.get("content-type", "")


def test_the_setup_page_route_is_reachable_before_it_is_configured(client, blank_deployment):
    """`/setup` 是前端的一條路由，不是伺服器上的檔案，所以它走 SPA fallback。

    分開驗：`/` 通了不代表它通——擋人的如果是一份路徑白名單，很容易只放行了根目錄。
    """
    resp = client.get("/setup")

    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


def test_the_page_can_load_what_it_needs_to_run(client, blank_deployment):
    """光是 index.html 出得來還不夠。

    擋掉 /assets 的話他拿到的是一片白，而白畫面比 503 更難查——沒有訊息、沒有狀態
    碼，只有一個看起來壞掉的網站。
    """
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/manifest.webmanifest").status_code == 200


def test_the_api_is_still_locked_while_it_is_unconfigured(client, blank_deployment):
    """放行畫面**不可以**變成放行 API。

    設定模式存在的理由是「沒有 JWT_SECRET 的時候，任何人都能簽出一張登入權杖」。
    為了讓畫面出得來而把整個鎖拿掉，換到的是一個沒有門的 app。
    """
    resp = client.get("/api/strategies")

    assert resp.status_code == 503
    assert resp.json()["setup_required"] is True


def test_the_setup_endpoints_still_answer(client, blank_deployment):
    """而那一頁要問得到「我還缺什麼」。"""
    assert client.get("/api/setup/status").status_code == 200


# --- 建置這個映像檔的每一個地方，都要給對 context ---------------------------


def _root():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent.parent


def test_the_dockerfile_needs_both_halves():
    """基準線：Dockerfile 真的會去拿 frontend/。

    沒有這一條，底下兩條會在「有人把 Dockerfile 改回只建後端」的時候還是綠的——它
    們守的是「context 要給根目錄」，而那個要求的**理由**在這裡。
    """
    dockerfile = (_root() / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY frontend/" in dockerfile, "Dockerfile 不再需要前端了？那底下兩條就沒有意義了"
    assert "COPY backend/" in dockerfile


def test_every_build_uses_the_repo_root_as_context():
    """**這一條是這次 CI 紅掉的原因。**

    我改了 render.yaml 和 docker-compose.yml，漏了 CI——而 CI 是唯一一個真的把這個
    映像檔建起來的地方，所以那是唯一會發現的地方。錯誤訊息是
    `"/backend": not found`，一個要讀三層才對得起來的訊息。

    這三個檔案之間沒有任何連結，而它們必須同意同一件事。
    """
    ci = (_root() / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    render = (_root() / "render.yaml").read_text(encoding="utf-8")
    compose = (_root() / "docker-compose.yml").read_text(encoding="utf-8")

    assert "docker build -f backend/Dockerfile" in ci, "CI 沒有用根目錄當 context"
    assert "./backend" not in ci.split("docker build")[1].split("\n")[0], (
        "CI 的 docker build 還指著 ./backend"
    )
    assert "dockerContext: ." in render, "render.yaml 的 context 不是根目錄"
    assert "context: ." in compose, "compose 的 context 不是根目錄"

    # **第四個地方，而它是最安靜的一個。** infra/main.tf 宣告的是維護者自己那一份
    # Render 服務。它沒有被跑過（見 CLAUDE.md 的工程標準表），所以錯了不會有任何東
    # 西變紅——直到有人第一次 `terraform apply`，然後把已經修好的那一格種回去。
    #
    # 那一格種回去的後果剛剛量過：build 失敗、線上留在舊版、而我們這邊只看得到「部
    # 署沒送達」。它花了六輪才找到，因為後台的 dockerfilePath 是對的，只有 context
    # 不是——一個看起來已經排除掉的假設。
    terraform = (_root() / "infra" / "main.tf").read_text(encoding="utf-8")
    docker_block = terraform.split("docker = {", 1)[1].split("}", 1)[0]
    assert '"./backend"' not in docker_block, (
        "infra/main.tf 還把 build context 宣告成 ./backend——apply 下去會把 #53 種回來"
    )
    assert 'context     = "."' in docker_block or 'context = "."' in docker_block, (
        "infra/main.tf 的 build context 不是根目錄"
    )


def test_the_guide_tells_an_existing_deployment_how_to_fix_its_build():
    """**第五個地方是一個人，而上面那條測試碰不到他。**

    平台上那個服務的 dockerContext ／ dockerfilePath 是**建立當下抄過去的一份**，不是
    每次去讀 render.yaml。所以這四個檔案改對了只保護到「之後才部署的人」；已經在跑的
    那些，下一次 build 直接失敗，錯誤訊息是

        "/backend/requirements.lock": not found

    而症狀在我們這邊只看得到「部署沒送達」。#53 花了整整六輪，因為後台的
    dockerfilePath 是對的、只有 context 不是——一個看起來已經排除掉的假設。

    對使用者來說更糟：Render 會繼續服務上一個成功的版本，所以**提醒照發、什麼都沒
    壞**，只是他從此再也拿不到更新，包括安全修補。那正是這個 repo 最不想要的失效形
    狀——安靜的那一種。

    他不是工程師，也不會來讀 render.yaml。所以修法只能是「在他會去的那一頁上，用他看
    得到的那個錯誤訊息當索引，說出要改哪兩格」。
    """
    guide = (_root() / "DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "requirements.lock" in guide, (
        "他在 build log 上看到的就是這串字，而那是他唯一能拿來搜尋的東西"
    )
    assert "dockerContext" in guide or "Docker Build Context" in guide
    assert "Dockerfile Path" in guide or "dockerfilePath" in guide
    # 兩格都要說。#53 卡住的原因正是只對到一格就以為兩格都對了。
    assert "./backend/Dockerfile" in guide


def test_no_path_in_the_guide_builds_from_the_backend_folder():
    """引導頁上**每一條**部署的路都要用專案根目錄當 build context。

    Render 那條修好之後，另外三條還留著舊的說法：Railway 的「Root Directory 填
    backend」、Fly 的「在 backend 資料夾裡執行 fly launch」。兩個都是把 context 設成
    backend/——跟花了六輪才找到的那一顆是同一顆地雷，而照做的人會拿到
    `"/backend/requirements.lock": not found`，一個他無法翻譯的訊息。

    這個 repo 有一整條「部署不是一家公司的事」的規則（CLAUDE.md、
    test_hosting_is_not_one_company.py）。那條規則要成立，替代路徑就得**真的走得
    通**，不是只是被列出來。
    """
    page = (_root() / "docs" / "install.html").read_text(encoding="utf-8")

    # 「把 backend 當成根目錄／工作目錄」的各種說法。指令本身帶 `backend/` 是對的
    # （例如 -f backend/Dockerfile），所以比對的是「進到那個資料夾」這個動作。
    forbidden = [
        "Root Directory</strong> 填 <code>backend</code>",
        "在 <code>backend</code> 資料夾",
        "cd backend",
    ]
    for phrase in forbidden:
        assert phrase not in page, f"引導頁還在叫人從 backend/ 建：{phrase}"


def test_the_guide_teaches_the_command_that_actually_works():
    """引導頁教的那一行，要真的建得起來。

    這個 repo 已經被「驗文案不驗事實」咬過：頁面叫人跑 `docker compose up`，而
    repo 裡根本沒有 compose 檔，測試卻是綠的。這裡守同一件事的另一半——頁面上那行
    指令如果還寫著在 backend 資料夾裡建，照做的人會拿到跟這次 CI 一樣的錯。
    """
    page = (_root() / "docs" / "install.html").read_text(encoding="utf-8")

    assert "docker build -f backend/Dockerfile" in page


def test_the_image_puts_the_frontend_where_main_py_looks_for_it():
    """一邊是寫死的，一邊是算出來的，而它們必須是同一個路徑。

    Dockerfile：`COPY --from=frontend /build/dist /frontend/dist`
    main.py：   `Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"`

    兩者之間沒有任何連結。改一次 WORKDIR、把 app/ 往下搬一層，使用者打開他唯一拿到
    的那個網址會看到 404——而 `/healthz` 每一項都還是綠的，因為後端本身完全沒事。

    CI 的 first-deploy 會用真的容器問這件事，但那要等到推上去；這一條在本機就答得出
    來，而且它說得出**哪一邊**動了。
    """
    import re
    from pathlib import PurePosixPath

    dockerfile = (_root() / "backend" / "Dockerfile").read_text(encoding="utf-8")
    main_py = (_root() / "backend" / "app" / "main.py").read_text(encoding="utf-8")

    # 最後一個 stage 的 WORKDIR，就是 `COPY backend/ .` 的落點。
    workdir = re.findall(r"^WORKDIR\s+(\S+)", dockerfile, re.MULTILINE)[-1]
    dest = re.search(r"^COPY --from=frontend\s+\S+\s+(\S+)", dockerfile, re.MULTILINE)
    assert dest, "Dockerfile 不再從前端那個 stage 複製 dist 了"

    # main.py 在容器裡的位置，以及它從那裡往上數幾層。
    in_container = PurePosixPath(workdir) / "app" / "main.py"
    hops = (
        len(
            re.search(r"Path\(__file__\)\.resolve\(\)((?:\.parent)+)", main_py)
            .group(1)
            .split(".parent")
        )
        - 1
    )
    root = in_container
    for _ in range(hops):
        root = root.parent

    assert str(root / "frontend" / "dist") == dest.group(1), (
        f"main.py 會去 {root / 'frontend' / 'dist'} 找，但映像檔把它放在 {dest.group(1)}"
    )


def test_the_ignore_file_is_where_the_context_root_is_now():
    """**改 context 會讓 .dockerignore 安靜地失效。**

    Docker 只讀**建置 context 根目錄**的那一份 `.dockerignore`。context 從 `backend/`
    搬到專案根目錄之後，`backend/.dockerignore` 就再也不會被讀到了——沒有警告、沒有
    錯誤，映像檔照樣建得起來，只是突然多了一堆東西。

    多的東西裡有 `backend/.env`。DEPLOYMENT.md 現在教的正是「在專案根目錄 docker
    build」，所以維護者自己或任何在本機建的人，會把一份含金鑰的 .env 烤進映像檔裡。
    這個 repo 是公開的，而映像檔會被推到某個 registry。
    """
    root = _root()
    ignore = root / ".dockerignore"

    assert ignore.is_file(), (
        "建置 context 是專案根目錄，但根目錄沒有 .dockerignore——"
        "backend/.dockerignore 已經不會被讀到了"
    )

    patterns = [
        line.strip()
        for line in ignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    # 會把秘密或本機資料帶進映像檔的，一個都不能少。
    for must in ("**/.env", "**/venv/", "**/*.db", "**/node_modules/"):
        assert must in patterns, f".dockerignore 少了 {must}"


def test_both_halves_get_a_dependency_vulnerability_check():
    """後端有 pip-audit，前端一直沒有對應的東西。

    Dependabot 兩邊都涵蓋（.github/dependabot.yml 有 pip 和 npm），所以前端不是沒守
    ——缺的是**推送當下的能見度**。而這個 repo 已經因為「兩半不對稱」出過事：更新的
    路徑曾經只有後端有、前端那半是斷的（#52／#53）。

    兩邊都要是**通知不是關卡**，理由是 ci.yml 對 pip-audit 寫的那一句：一個傳遞相依套
    件裡的 CVE 要等別人發版，擋住 build 只會連帶擋掉一個無關的修復——而這個專案的最高
    優先是警告不能停擺，一個修通知路徑的 hotfix 不可以被別人家的 CVE 卡住。

    **解析 YAML，不要在原始碼裡找字串。** 第一版用 `ci.index("pip-audit")` 找位置，而
    那個字第一次出現是在 SAST 步驟的註解裡（「A GATE, unlike pip-audit below」）——於是
    它去檢查了一個不相干的步驟，然後對著一個完全正確的設定檔報錯。
    """
    import yaml

    ci = yaml.safe_load((_root() / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = [step for job in ci["jobs"].values() for step in (job.get("steps") or [])]

    for tool in ("pip-audit", "npm audit"):
        running = [s for s in steps if tool in (s.get("run") or "")]
        assert running, f"CI 裡沒有真的執行 {tool} 的步驟"
        for step in running:
            assert step.get("continue-on-error") is True, (
                f"{tool} 是硬性關卡——別人家的 CVE 會擋住修通知路徑的 hotfix"
            )
