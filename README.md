<h1>自媒体发稿工作台 · 代码包</h1>
<blockquote>
<p>本包汇总了"飞书多维表格 + Python 发布执行器"方案涉及的全部代码与配置，按用途分类，便于下载、备份与部署。</p>
</blockquote>
<h2>一、目录结构</h2>
<pre><code class="hljs language-plaintext">自媒体发稿工作台-代码包/
├─ 01_发布执行器/      ← 核心运行代码
│   └─ publish_executor.py     （飞书读表 → 三平台API发布 → 回写状态；Webhook + 定时双模式）
├─ 02_依赖与配置/      ← 运行所需依赖与密钥模板
│   ├─ requirements.txt       （flask / requests / python-dotenv）
│   └─ .env.example           （所有密钥占位，复制为 .env 后填真实值）
├─ 03_技能说明/        ← 方案总说明（Skill 文档）
│   └─ SKILL.md               （可行性边界 / 架构 / 字段设计 / 写稿提示词 / 分阶段路线 / 安全规范）
└─ README.md           ← 本文件
</code></pre>
<h2>二、各文件作用</h2>
<table>
<thead>
<tr>
<th>文件</th>
<th>作用</th>
</tr>
</thead>
<tbody><tr>
<td><code class="inline-code">publish_executor.py</code></td>
<td>发布执行器主体：飞书多维表格读写（tenant_access_token 自动刷新 + 分页）、百家号/网易号/头条号官方 API 发布、指数退避重试、飞书群机器人失败告警、<code class="inline-code">/publish</code> 与 <code class="inline-code">/health</code> Webhook 入口、<code class="inline-code">--serve</code>/<code class="inline-code">--once</code> 双模式</td>
</tr>
<tr>
<td><code class="inline-code">requirements.txt</code></td>
<td>Python 依赖清单，部署前 <code class="inline-code">pip install -r requirements.txt</code></td>
</tr>
<tr>
<td><code class="inline-code">.env.example</code></td>
<td>密钥与字段名映射模板；<strong>复制为 <code class="inline-code">.env</code> 后逐项填值</strong>，密钥只放这里，绝不硬编码</td>
</tr>
<tr>
<td><code class="inline-code">SKILL.md</code></td>
<td>整套工作台的设计说明：可行性边界、整体架构、飞书字段设计、写稿提示词、分阶段落地路线、安全纪律、核查清单</td>
</tr>
</tbody></table>
<h2>三、能真·自动发布的平台（v1）</h2>
<ul>
<li><strong>百家号</strong>：官方内容 API（app_id + app_token）</li>
<li><strong>网易号</strong>：网易号媒体开放平台（access_token + 签名）</li>
<li><strong>头条号</strong>：字节/头条开放平台 OAuth2（access_token 仅 2 小时，生产需加刷新）</li>
</ul>
<blockquote>
<p>其余平台（知乎/搜狐/一点/大鱼/企鹅/博客园/东方财富/同顺等）<strong>无官方发布接口</strong>，v1 不自动发，只把"需人工发布"写回回执。若要走全自动，需依赖 Cookie/RPA 或第三方聚合工具（有封号/违规风险，工具方免责）。</p>
</blockquote>
<h2>四、最简部署步骤</h2>
<pre><code class="hljs language-bash"><span class="hljs-comment"># 1. 安装依赖</span>
pip install -r 02_依赖与配置/requirements.txt

<span class="hljs-comment"># 2. 配置密钥</span>
<span class="hljs-built_in">cp</span> 02_依赖与配置/.env.example .<span class="hljs-built_in">env</span>   <span class="hljs-comment"># 然后逐项填好真实值</span>

<span class="hljs-comment"># 3. 启动</span>
python 01_发布执行器/publish_executor.py --serve   <span class="hljs-comment"># Webhook 常驻（Flask）</span>
<span class="hljs-comment"># 或定时跑一次（可用云函数定时器 / cron 触发）</span>
python 01_发布执行器/publish_executor.py --once
</code></pre>
<p>飞书侧：多维表格自动化「当 审核状态 = 已通过 → 发送 Webhook → 你的服务地址 + /publish」。</p>
<h2>五、重要提醒（务必看）</h2>
<ol>
<li><strong>密钥安全</strong>：所有 AppID / Secret / Token 只放 <code class="inline-code">.env</code> 或云函数环境变量，<strong>绝不写进代码、绝不提交到仓库</strong>。</li>
<li><strong>人工确认</strong>：对外发送属高风险操作，默认"审核状态=已通过"才发布；跑稳前不要关闭该开关。</li>
<li><strong>头条号 token</strong>：access_token 仅 2 小时有效，正式运行务必补上刷新逻辑（见 <code class="inline-code">publish_executor.py</code> 内 <code class="inline-code">get_toutiao_token</code> 的 TODO）。</li>
<li><strong>无接口平台风险</strong>：自动发无接口平台 = Cookie 模拟登录，平台视为违规，可能降权/封号。</li>
</ol>
<h2>六、关于本包的说明</h2>
<ul>
<li>本代码包由工作台搭建会话中保留的 Skill 目录（<code class="inline-code">self-media-publish-workbench</code>）恢复整理而来。</li>
<li><strong>原工作区曾因环境重置被清空</strong>，其中两份 HTML 文档（《自媒体自动发稿工作台_纯点击搭建指南》《国内自媒体平台目录_个人注册与发布接口》）属于图文说明，不在"代码"范畴，未在包内；如需要可单独重新生成。</li>
<li>平台接口、开放政策变动频繁，接入前请以各平台<strong>最新官方文档</strong>为准。</li>
</ul>
