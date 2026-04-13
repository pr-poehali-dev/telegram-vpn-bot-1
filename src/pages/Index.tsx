import Icon from "@/components/ui/icon";

export default function Index() {
  return (
    <div className="min-h-screen bg-background flex flex-col items-center justify-center px-4">
      {/* Header */}
      <div className="w-full max-w-md mb-10 text-center animate-fade-in">
        <div className="inline-flex items-center gap-2 mb-4 px-3 py-1.5 rounded-sm border border-border bg-muted text-xs text-muted-foreground tracking-widest uppercase font-mono">
          <span className="w-1.5 h-1.5 rounded-full bg-green-500 inline-block animate-pulse"></span>
          Система активна
        </div>
        <h1 className="text-3xl font-semibold tracking-tight text-foreground mb-2">
          KeyBot
        </h1>
        <p className="text-muted-foreground text-sm leading-relaxed">
          Корпоративная система выдачи ключей доступа<br/>через Telegram
        </p>
      </div>

      {/* Card */}
      <div className="w-full max-w-md border border-border rounded-sm bg-card animate-scale-in" style={{animationDelay: '0.1s', opacity: 0, animationFillMode: 'forwards'}}>
        {/* Card header */}
        <div className="border-b border-border px-6 py-4 flex items-center gap-3">
          <div className="w-8 h-8 rounded-sm bg-primary/10 flex items-center justify-center">
            <Icon name="Bot" size={16} className="text-primary" />
          </div>
          <div>
            <div className="text-sm font-medium text-foreground">Telegram бот</div>
            <div className="text-xs text-muted-foreground font-mono">@RossoVPN_bot</div>
          </div>
          <div className="ml-auto flex items-center gap-1.5 text-xs text-green-400 font-mono">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 inline-block"></span>
            Online
          </div>
        </div>

        {/* Steps */}
        <div className="px-6 py-5 space-y-4">
          <p className="text-xs text-muted-foreground uppercase tracking-widest font-mono mb-4">Порядок получения ключа</p>

          {[
            { icon: "MessageSquare", step: "01", title: "Запустите бота", desc: 'Отправьте команду /start в Telegram' },
            { icon: "User", step: "02", title: "Введите имя", desc: "Укажите ваше имя для идентификации" },
            { icon: "Key", step: "03", title: "Получите ключ", desc: "Нажмите кнопку и получите VLESS ключ доступа" },
          ].map((item, i) => (
            <div key={i} className="flex items-start gap-4 group">
              <div className="flex-shrink-0 w-8 h-8 rounded-sm border border-border bg-muted flex items-center justify-center group-hover:border-primary/50 transition-colors">
                                <Icon name={item.icon} size={14} className="text-muted-foreground group-hover:text-primary transition-colors" />
              </div>
              <div className="flex-1 pt-0.5">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-mono text-primary/60">{item.step}</span>
                  <span className="text-sm font-medium text-foreground">{item.title}</span>
                </div>
                <p className="text-xs text-muted-foreground mt-0.5">{item.desc}</p>
              </div>
            </div>
          ))}
        </div>

        {/* CTA */}
        <div className="border-t border-border px-6 py-4">
          <a
            href="https://t.me/RossoVPN_bot"
            target="_blank"
            rel="noopener noreferrer"
            className="w-full flex items-center justify-center gap-2 bg-primary text-primary-foreground text-sm font-medium py-2.5 px-4 rounded-sm hover:bg-primary/90 transition-colors"
          >
            <Icon name="Send" size={15} />
            Открыть бота в Telegram
          </a>
        </div>
      </div>

      {/* Footer */}
      <div className="mt-8 text-xs text-muted-foreground font-mono animate-fade-in" style={{animationDelay: '0.3s', opacity: 0, animationFillMode: 'forwards'}}>
        VPN · VLESS Reality · 3x-ui
      </div>
    </div>
  );
}