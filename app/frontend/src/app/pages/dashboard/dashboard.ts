import { ChangeDetectionStrategy, Component } from '@angular/core';

interface Metric {
  label: string;
  value: string;
  hint: string;
}

@Component({
  selector: 'app-dashboard',
  templateUrl: './dashboard.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class DashboardComponent {
  metrics: Metric[] = [
    { label: 'Revenue', value: '$42.5k', hint: 'Last 30 days' },
    { label: 'Signups', value: '1,204', hint: 'Last 30 days' },
    { label: 'Churn', value: '2.1%', hint: 'Last 30 days' },
    { label: 'NPS', value: '64', hint: 'Last survey' },
  ];
}
