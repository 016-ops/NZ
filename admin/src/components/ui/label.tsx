/** 官方 shadcn/ui 4.21.4 Radix/nova 组件；中文注释说明其职责，业务层仅组合使用。 */
import * as React from "react"
import { cn } from "cn"
import { Label as LabelPrimitive } from "radix-ui"

/** 使用 Radix Label 关联输入控件，保留键盘和辅助技术的标签语义。 */
function Label({
  className,
  ...props
}: React.ComponentProps<typeof LabelPrimitive.Root>) {
  return (
    <LabelPrimitive.Root
      data-slot="label"
      className={cn(
        "flex items-center gap-2 text-sm leading-none font-medium select-none group-data-[disabled=true]:pointer-events-none group-data-[disabled=true]:opacity-50 peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
        className
      )}
      {...props}
    />
  )
}

export { Label }
