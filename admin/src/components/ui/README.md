# UI 组件来源与维护

本目录由官方 `shadcn@4.21.4` CLI 从 Radix/nova registry 生成。业务页面通过这些组件组合，保持主题、键盘交互、焦点管理与语义属性一致。

所需 Tailwind 状态变体固定在 `src/ui-variants.css`。项目不依赖完整 shadcn CLI 包；以后补组件使用 `npx shadcn@4.21.4 add <组件> --yes`，然后核对组件新增的状态工具、补中文注释并重新构建/验收。

生成组件来自 shadcn/ui（MIT），Radix 与图标依赖仍由 package-lock.json 锁定。新增组件须沿用 components.json 的 preset 和 alias。
