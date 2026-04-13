## 📋 QQ 群防御插件需求文档（版本1.0）

### 🎯 背景
- QQ 游戏群经常遇到恶意用户进群后私聊群员，造成骚扰。  
- 管理员人工审核难以识别潜伏者，需要自动化处理机制。  

### 🛠️ 核心功能
1. **举报机制**
   - 群员通过固定格式消息举报：  
     ```
     有内鬼 @某人
     ```
   - 插件监听群消息，解析目标用户 ID。

2. **举报计数**
   - 每个目标用户维护一个举报集合（存储举报者 ID）。  
   - 同一个人不能重复举报同一个目标。  

3. **阈值判断**
   - 当某个用户被举报人数 ≥ 阈值（默认 2 人，可配置），机器人立即踢出该用户。  

4. **提示与透明化**
   - 每次举报都会在群里提示：  
     ```
     用户 @举报者 举报了 @目标。当前举报人数：X
     ```
   - 达到阈值后提示：  
     ```
     用户 @目标 因被多人举报，已被移出群聊。
     ```

---

### ⚙️ 插件配置参数
- `threshold`: 举报人数阈值（默认 2）。  
- `reportKeyword`: 举报触发关键词（默认“有内鬼”）。  

---

### 📝 插件逻辑伪代码
```ts
const reports = new Map<string, Set<string>>();

function handleReport(bot, groupId, reporterId, targetId) {
  if (!reports.has(targetId)) {
    reports.set(targetId, new Set());
  }
  const reporters = reports.get(targetId)!;

  if (reporters.has(reporterId)) {
    bot.sendGroupMsg(groupId, `[举报系统] 你已经举报过该用户。`);
    return;
  }

  reporters.add(reporterId);
  bot.sendGroupMsg(groupId, `[举报系统] 用户 ${reporterId} 举报了 ${targetId}。当前举报人数：${reporters.size}`);

  const threshold = 2; // 可配置
  if (reporters.size >= threshold) {
    bot.kickGroupMember(groupId, targetId, "多人举报");
    bot.sendGroupMsg(groupId, `[举报系统] 用户 ${targetId} 因被多人举报，已被移出群聊。`);
    reports.delete(targetId);
  }
}

bot.on("group_message", (event) => {
  const msg = event.message.trim();
  if (msg.startsWith("有内鬼")) {
    const targetId = msg.split(" ")[1]; // 简单解析 @某人
    handleReport(bot, event.group_id, event.user_id, targetId);
  }
});
```

---

### ✅ 总结
- **输入**：群员发送“有内鬼 @某人”。  
- **处理**：机器人记录举报，达到阈值后直接踢人。  
- **输出**：群内提示举报进度与踢人结果。  
- **可配置**：举报阈值、关键词。  



要不要我再帮你写一个 **README.md 模板**，让你可以直接放到 GitHub 仓库里？
