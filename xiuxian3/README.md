# 修仙项目

本仓库包含修仙后端和机器人转发插件：

```text
xiuxianserver/    FastAPI + WebSocket 修仙后端
xiuxianplugin/    NoneBot 机器人转发插件
```

日常功能开发以 `xiuxianserver/修仙` 业务组件为主。排查问题时需要看完整项目链路，包括后端、框架层和插件；默认不修改 `xiuxianplugin`，也不修改 `xiuxianserver/launch`、`auto`、Adapter、生命周期和路由加载等框架层代码。

项目和框架维护边界见 `xiuxianserver/开发约束.md`；修仙组件开发约束见 `xiuxianserver/修仙/开发约束.md`。服务端玩法、命令和回复规则以代码为准，组件说明 Markdown 只记录业务边界和扩展约束。

![项目Logo](help.png)
