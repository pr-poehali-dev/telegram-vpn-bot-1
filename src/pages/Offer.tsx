import Icon from "@/components/ui/icon";
import { Link } from "react-router-dom";

export default function Offer() {
  return (
    <div className="min-h-screen bg-background px-4 py-10">
      <div className="w-full max-w-2xl mx-auto">

        {/* Back */}
        <Link to="/" className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground font-mono mb-8 transition-colors">
          <Icon name="ArrowLeft" size={13} />
          Главная
        </Link>

        {/* Header */}
        <div className="mb-8">
          <div className="inline-flex items-center gap-2 mb-4 px-3 py-1.5 rounded-sm border border-border bg-muted text-xs text-muted-foreground tracking-widest uppercase font-mono">
            <Icon name="FileText" size={12} />
            Публичная оферта
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground mb-2">
            Условия использования RossoVPN
          </h1>
          <p className="text-sm text-muted-foreground">
            Редакция от 14 апреля 2026 г.
          </p>
        </div>

        {/* Content */}
        <div className="space-y-6 border border-border rounded-sm bg-card divide-y divide-border">

          <Section title="1. Предмет оферты">
            <p>Настоящая публичная оферта является официальным предложением ИП / физического лица, предоставляющего услуги под торговой маркой <strong>RossoVPN</strong>, на оказание услуг доступа к VPN-сервису на условиях ежемесячной автоматически возобновляемой подписки.</p>
            <p>Оплата подписки означает полное и безоговорочное принятие (акцепт) условий настоящей оферты.</p>
          </Section>

          <Section title="2. Услуга и стоимость">
            <p>Тариф: <strong>Базовый — 199 ₽ в месяц</strong>.</p>
            <ul>
              <li>Безлимитный трафик</li>
              <li>Высокая скорость соединения</li>
              <li>Протокол VLESS Reality (3x-ui)</li>
              <li>Доступ к VPN-ключу через Telegram-бот @RossoVPN_bot</li>
            </ul>
            <p>Доступ предоставляется немедленно после успешной оплаты.</p>
          </Section>

          <Section title="3. Автоплатежи — условия">
            <p>При оформлении подписки пользователь соглашается на <strong>автоматическое списание</strong> 199 ₽ каждые 30 дней с привязанного способа оплаты.</p>
            <ul>
              <li>Списание происходит в дату, соответствующую дате первой оплаты</li>
              <li>За 3 дня до списания пользователь получает уведомление в Telegram-боте</li>
              <li>Если списание не прошло — доступ приостанавливается до оплаты</li>
              <li>Привязанный способ оплаты хранится в защищённой системе ЮKassa и не передаётся третьим лицам</li>
            </ul>
          </Section>

          <Section title="4. Настройка и отключение автоплатежей">
            <p>Пользователь вправе в любой момент:</p>
            <ul>
              <li>Отключить автоплатёж — через команду <code>/cancel</code> в Telegram-боте @RossoVPN_bot</li>
              <li>Запросить отключение через поддержку: @btb75 или @makarevichas</li>
            </ul>
            <p>После отключения автоплатежа подписка продолжает действовать до конца оплаченного периода, после чего автоматически завершается без списаний.</p>
          </Section>

          <Section title="5. Возврат средств">
            <p>Возврат возможен в течение <strong>7 дней</strong> с момента оплаты при условии, что услуга фактически не использовалась (ключ не применялся для подключения).</p>
            <p>Для возврата обратитесь в поддержку: @btb75 или @makarevichas.</p>
          </Section>

          <Section title="6. Ответственность сторон">
            <p>Исполнитель обязуется обеспечивать доступность сервиса не менее 95% времени в месяц. В случае технических сбоев срок действия подписки продлевается соразмерно времени недоступности.</p>
            <p>Пользователь обязуется не использовать VPN в противоправных целях в соответствии с законодательством РФ.</p>
          </Section>

          <Section title="7. Прочее">
            <p>Исполнитель вправе изменять условия оферты с уведомлением пользователей через Telegram-бот не менее чем за 7 дней.</p>
            <p>Вопросы и обращения: @btb75 · @makarevichas</p>
          </Section>

        </div>

        <div className="mt-8 text-xs text-muted-foreground font-mono text-center">
          RossoVPN · @RossoVPN_bot · 2026
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="px-6 py-5">
      <h2 className="text-sm font-semibold text-foreground mb-3 font-mono">{title}</h2>
      <div className="text-sm text-muted-foreground space-y-2 leading-relaxed [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:space-y-1 [&_strong]:text-foreground [&_code]:bg-muted [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded-sm [&_code]:text-xs [&_code]:font-mono">
        {children}
      </div>
    </div>
  );
}
