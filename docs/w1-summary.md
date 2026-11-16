# W1 复习总结（2026-11-11 ~ 11-16）

> 用途：面试前快速过一遍。每个知识点都按“一句话结论 → 展开 → 坑”组织。
> 自测题在最后，答案附在题后，先遮住答案自己讲一遍。

---

## 0. W1 完成了什么

| 项 | 结果 |
|---|---|
| 刷题 | 15 / 15 |
| 八股 | HTTP/TCP、索引、事务 |
| 项目 | 跑通 ResearchScout、source 贯通修复、agent_llm 精读、架构图、评测基线 |
| 评测 | pytest 482 passed；Agent 14/14；记忆 50/50 |

---

## 1. 刷题 15 题

### 1.1 按套路分类

| 套路 | 题目 |
|---|---|
| 哈希 / 集合 | 1 两数之和、128 最长连续序列 |
| 双指针 | 11 盛水、15 三数之和、16 最接近三数和、26 删除重复项、88 合并有序数组、189 轮转、238 除自身乘积、283 移动零 |
| DP / 贪心 | 53 最大子数组、121 买卖股票 |
| 排序 | 56 合并区间 |
| 位运算 | 136 只出现一次 |
| 投票 | 169 多数元素 |

### 1.2 逐题钥匙

#### 1. 两数之和

哈希存“值 → 下标”，**先查 `target-x`，再存 x**。

```python
def two_sum(nums, target):
    seen = {}
    for i, x in enumerate(nums):
        if target - x in seen:
            return [seen[target - x], i]
        seen[x] = i
```

坑：先存后查会让 `[3,3], 6` 返回 `[0,0]`。O(n)/O(n)。

#### 11. 盛最多水的容器

双指针从两端收，**每次移动较矮的一边**。

```python
l, r, best = 0, len(h) - 1, 0
while l < r:
    best = max(best, min(h[l], h[r]) * (r - l))
    if h[l] < h[r]: l += 1
    else: r -= 1
```

为什么：矮边决定了当前高度，移动它才可能变高；移高边只会让宽度变小。O(n)/O(1)。

#### 15. 三数之和

排序 + 固定 i + 双指针，**三处去重**。

```python
nums.sort()
for i in range(len(nums) - 2):
    if nums[i] > 0: break
    if i > 0 and nums[i] == nums[i-1]: continue
    l, r = i + 1, len(nums) - 1
    while l < r:
        s = nums[i] + nums[l] + nums[r]
        if s < 0: l += 1
        elif s > 0: r -= 1
        else:
            res.append([nums[i], nums[l], nums[r]])
            while l < r and nums[l] == nums[l+1]: l += 1
            while l < r and nums[r] == nums[r-1]: r -= 1
            l += 1; r -= 1
```

坑：`if` 写成 `if` 而不是 `elif`，`else` 会绑到第二个 `if`，把和不为 0 的三元组也加进去。O(n²)/O(1)。

#### 16. 最接近的三数之和

同 15，但**每一步都更新 best**，不是命中才更新。

```python
best = float("inf")
...
s = nums[i] + nums[l] + nums[r]
if abs(s - target) < abs(best - target): best = s
if s == target: return target
elif s < target: l += 1
else: r -= 1
```

坑：`best` 初始化成 0 会挡住正确答案；忘了 `abs`。O(n²)/O(1)。

#### 26. 删除有序数组中的重复项

快慢指针，**不同才写**。

```python
k = 1
for i in range(1, len(nums)):
    if nums[i] != nums[k-1]:
        nums[k] = nums[i]
        k += 1
return k
```

坑：比较条件写反（`==`）；空数组。O(n)/O(1)。

#### 53. 最大子数组和（Kadane）

`cur = max(x, cur + x)`，`best` 用 `nums[0]` 初始化。

```python
cur = best = nums[0]
for x in nums[1:]:
    cur = max(x, cur + x)
    best = max(best, cur)
return best
```

为什么：前面的和是负数就丢掉重开。坑：`best=0` 在全负数时错。O(n)/O(1)。

#### 56. 合并区间

按左端排序；`start > last_end` 新建，否则 `max` 右端。

```python
intervals.sort(key=lambda x: x[0])
res = []
for s, e in intervals:
    if not res or s > res[-1][1]: res.append([s, e])
    else: res[-1][1] = max(res[-1][1], e)
return res
```

坑：不排序；合并时直接赋值 `= e`，遇到被包含区间会错。O(n log n)/O(n)。

#### 88. 合并两个有序数组

**从后往前**双指针，避免覆盖 `nums1` 未处理元素。

```python
i, j, k = m - 1, n - 1, m + n - 1
while i >= 0 and j >= 0:
    if nums1[i] > nums2[j]: nums1[k] = nums1[i]; i -= 1
    else: nums1[k] = nums2[j]; j -= 1
    k -= 1
while j >= 0:
    nums1[k] = nums2[j]; j -= 1; k -= 1
```

坑：从前往后会覆盖；忘了补 `nums2` 剩余。O(m+n)/O(1)。

#### 121. 买卖股票的最佳时机

维护历史最低价。

```python
low, best = float("inf"), 0
for p in prices:
    low = min(low, p)
    best = max(best, p - low)
return best
```

坑：先卖后买；排序打乱时间。O(n)/O(1)。

#### 128. 最长连续序列

全部进 set，**只从 `x-1` 不存在的起点**往上数。

```python
s = set(nums)
best = 0
for x in s:
    if x - 1 not in s:
        y = x
        while y + 1 in s: y += 1
        best = max(best, y - x + 1)
return best
```

坑：不判断起点会 O(n²)。“连续”指数值连续，不是下标相邻。O(n)/O(n)。

#### 136. 只出现一次的数字

全部异或。

```python
res = 0
for x in nums: res ^= x
return res
```

为什么：`a^a=0`、`a^0=a`，成对抵消。O(n)/O(1)。

#### 169. 多数元素

Boyer-Moore 投票：相同 +1，不同 -1，归零换人。

```python
cand, count = None, 0
for x in nums:
    if count == 0: cand = x
    count += 1 if x == cand else -1
return cand
```

为什么：多数元素超过一半，抵消不完。坑：题目不保证多数时要二次验证。O(n)/O(1)。

#### 189. 轮转数组

三次反转：整体 → 前 k → 后 n-k，`k %= n`。

```python
k %= n
def rev(l, r):
    while l < r:
        nums[l], nums[r] = nums[r], nums[l]
        l += 1; r -= 1
rev(0, n-1); rev(0, k-1); rev(k, n-1)
```

坑：`new = nums` 是引用不是复制；`k > n` 要取模。O(n)/O(1)。

#### 238. 除自身以外数组的乘积

前缀积 + 后缀积，不用除法。

```python
res = [1] * n
left = 1
for i in range(n):
    res[i] = left
    left *= nums[i]
right = 1
for i in range(n-1, -1, -1):
    res[i] *= right
    right *= nums[i]
return res
```

坑：两遍的顺序都不能反；`res` 初始化为 1。O(n)/O(1)。

#### 283. 移动零

写指针，非零前移，最后补 0。

```python
k = 0
for x in nums:
    if x != 0:
        nums[k] = x
        k += 1
for i in range(k, len(nums)): nums[i] = 0
```

坑：边遍历边 `pop` 会漏元素；`remove` 是 O(n²)。O(n)/O(1)。

---

## 2. 八股

### 2.1 HTTP / TCP

**三次握手**

```
SYN → SYN+ACK → ACK
```

- 第 1 步：客户端发送能力正常；
- 第 2 步：服务端收、发能力正常，客户端知道对方收到了；
- 第 3 步：服务端知道客户端接收能力正常。

**为什么不是两次**：服务端发送能力未确认；历史重复 SYN 会建立客户端不认的连接，白占资源。
**为什么不是四次**：第二次的 `SYN+ACK` 合并了“确认你”和“我也连”。

**四次挥手**

```
FIN → ACK → FIN → ACK
```

TCP 全双工：客户端说“我发完了”，服务端可能还有数据，只能先 ACK，发完再 FIN，中间是**半关闭**。

**TIME_WAIT**：主动关闭方等 **2MSL**——保证最后一个 ACK 能到（对方重发 FIN 还能回），并让旧报文消散。2MSL = 一个来回。

**HTTP 版本**

| 版本 | 改进 | 遗留 |
|---|---|---|
| 1.1 | 持久连接 | 应用层队头阻塞 |
| 2 | 二进制分帧、多路复用、HPACK | TCP 层队头阻塞 |
| 3 | QUIC/UDP、0-RTT | 兼容性 |

**HTTPS**：非对称加密协商出对称密钥（+ 证书验身份），对称加密传数据。不全用非对称是因为慢。

**输入 URL**：DNS → TCP → TLS → HTTP → 服务端 → 渲染。

### 2.2 索引

- **B+ 树**：一页多键、树矮 IO 少；非叶子不存数据；叶子链表利于范围查询。
- **聚簇索引**：叶子存整行。**二级索引**：叶子存主键，查其他列要**回表**。
- **覆盖索引**：查询列都在索引里，不用回表。
- **最左前缀**：`(a,b,c)` 能用于 `a`、`a+b`、`a+b+c`；`a+c` 只用 a；`b` 用不了。
- **范围截断**：`a=1 and b>2 and c=3`，c 不能用于定位（b 是范围），但可走索引下推过滤。
- **失效**：函数、隐式转换、前导模糊 `%x`、OR 非索引列、区分度低。
- **explain**：type / key / rows / Extra。

### 2.3 事务

- **ACID**：A=undo log；D=redo log（WAL）；I=MVCC+锁；C 是目的。
- **隔离级别**：RU / RC / RR（默认）/ 串行化；对应脏读、不可重复读、幻读。
- **MVCC**：隐藏列 `trx_id`、`roll_pointer` → undo 版本链；ReadView 四字段 `m_ids/min/max/creator`。
- **可见性**：自己的可见；小于 min 可见；大于等于 max 不可见；在 m_ids 里不可见。
- **RC vs RR**：RC 每条 SELECT 新 ReadView；RR 第一次 SELECT 生成并复用。
- **幻读**：快照读靠 MVCC；当前读靠 next-key lock（记录锁+间隙锁）。
- **日志**：redo 崩溃恢复；undo 回滚+MVCC；binlog 主从/备份。

---

## 3. 项目（ResearchScout）

### 3.1 架构

```
Web/curl → FastAPI（鉴权/限流/SSE）
        → Agent 循环（LLM 选工具，≤3 轮/≤4 次/$0.03）
        → 8 个工具 + 三层记忆
        → SQLite（FTS5 + 向量）
```

### 3.2 Agent 循环

- 模型返回 `tool_calls`，代码执行后把结果以 `role=tool` 回填；
- 预算：轮数、调用数、美元、墙钟、用户取消；
- 预算用完：追加“不要再调工具”的指令，且**最终调用不传 tools**；
- 证据为空：`_finish` 直接改写为“没找到”，不让模型编。

### 3.3 记忆

- 短期：最近 4 条 + 摘要（≤600 字）；
- 长期：`mem_fact` 带 `valid_from/valid_to`，旧值不删除只失效；
- 三层：语义（事实）、情景（search_episode）、程序（RSI 策略）。

### 3.4 评测基线

| 指标 | 数值 |
|---|---|
| pytest | 482 passed / 14.88s |
| Agent | 14/14（`a-refuse-2` 不稳定） |
| 记忆 | 50/50 |
| 单次成本 | ≈ $0.0026 |
| p50 / p95 | 8.4s / 20.7s |

---

## 4. 自测清单（先遮住答案）

1. 三次握手每一步分别确认了什么？为什么两次不够？
2. 四次挥手为什么是四次？TIME_WAIT 为什么等 2MSL？
3. HTTP/2 的多路复用解决了什么？它自己又留下什么队头阻塞？
4. HTTPS 里对称和非对称各用在哪一步？为什么不全用非对称？
5. 输入 URL 后，DNS 在 TCP 之前还是之后？为什么？
6. 索引 `(a,b,c)`，`where b=1`、`where a=1 and c=2`、`where a=1 and b>2 and c=3` 分别用到几列？
7. 什么是回表？怎么避免？
8. RC 和 RR 的 ReadView 生成时机有什么区别？为什么 RR 可重复读？
9. 一条记录 `trx_id=5`，ReadView `m_ids=[3,7]`、`min=3`、`max=9`、`creator=10`，可见吗？
10. RR 下幻读怎么解决？快照读和当前读分别靠什么？
11. undo / redo / binlog 各保证什么？
12. 两数之和为什么必须先查后存？
13. 盛水为什么移动矮边？用“淘汰”解释。
14. Kadane 为什么是 `max(x, cur + x)`？
15. 轮转数组为什么三次反转能得到结果？
16. Agent 的预算有哪几个？为什么用完要“不传 tools”？
17. 为什么拒答守卫放在 `_finish`，而不是让模型自己判断？
18. `source` 贯通那个 bug 的教训是什么？

---

## 5. 自测答案

1. 第 1 步确认客户端能发；第 2 步确认服务端能收能发、客户端知道对方收到；第 3 步确认服务端知道客户端能收。两次不够：服务端发送能力未确认，且历史 SYN 会建无效连接。
2. 全双工，两个方向分别关闭；服务端收到 FIN 后可能还有数据，先 ACK，发完再 FIN。TIME_WAIT 2MSL：保最后一个 ACK 能到，并让旧报文消散。
3. 解决了应用层队头阻塞（多个请求复用一条连接交错传输）；TCP 层丢包仍会阻塞所有流，留给 HTTP/3 的 QUIC 解决。
4. 非对称用于协商对称密钥、验证证书；对称用于加密后续 HTTP 数据。不全用非对称是因为慢几个数量级。
5. DNS 在前。TCP 握手需要目的 IP，而用户输入的是域名。
6. `b=1` 用不到；`a=1 and c=2` 只用到 a；`a=1 and b>2 and c=3` 用到 a、b，c 被范围截断。
7. 二级索引叶子存主键，查非索引列要回聚簇索引取整行。用覆盖索引避免。
8. RC 每条 SELECT 新建 ReadView，能看到别人新提交的；RR 第一次 SELECT 生成并复用，所以可重复读。
9. 不可见：5 在 `[3,9)` 且在 `m_ids` 里（生成快照时未提交）。
10. 快照读靠 MVCC 看不见新行；当前读靠 next-key lock（记录锁+间隙锁）锁范围。
11. undo 保证原子性（回滚）并支撑 MVCC；redo 保证持久性（WAL，崩溃恢复）；binlog 用于主从复制和备份。
12. 避免同一个元素用两次：先查后存，`[3,3],6` 不会返回 `[0,0]`。
13. 矮边决定当前高度；以它为边界的容器，宽度只会变小、高度不会超过它，所以它不可能再贡献更大面积，可以淘汰。
14. 前面的和如果是负数，只会拖累当前元素；所以要么接上，要么从当前元素重开。
15. 整体反转把后 k 个挪到前面但顺序反了；再分别反转两段，把各自顺序转回来。
16. 轮数、调用数、美元、墙钟、用户取消。最终调用不传 tools 是从物理上禁止再调工具，不依赖模型听话。
17. 模型自评“有没有证据”不可靠；`has_items` 是客观事实，代码判生死。
18. 一个字段要在**所有出口**都带上；只改主路径会漏掉 facet/topic 路径，且合并时别覆盖来源字段。

---

## 6. 易错点 Top 8

1. `if` / `elif` 写错，`else` 绑错对象；
2. 边遍历边 `pop`，下标错位；
3. `new = nums` 是引用不是复制；
4. `best` 初始化成 0；
5. 排序解法忘了去重；
6. 范围截断说成“b 和 c 都截断”；
7. 记忆说明只在非拒答时追加；
8. 真实 key 写进 `.env.example`。

---

## 7. 下一步（W2）

1. `a-refuse-2` 跑 3 次，目标稳定 14/14；
2. 决定数据方案（全量库 / 评测小库 / 继续 demo）；
3. Agent 评测扩到 20–30 条；
4. 然后做检索或记忆的定向优化。
