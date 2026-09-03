import { ChangeDetectionStrategy, Component } from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';

import { IconName } from '../../icons';

interface StatCard {
  label: string;
  value: string;
  delta: string;
  icon: IconName;
}

@Component({
  selector: 'app-home',
  imports: [LucideAngularModule],
  templateUrl: './home.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class HomeComponent {
  stats: StatCard[] = [
    { label: 'Active users', value: '1,284', delta: '+12%', icon: 'User' },
    { label: 'Sessions', value: '8,932', delta: '+4%', icon: 'LayoutDashboard' },
    { label: 'Conversations', value: '342', delta: '+9%', icon: 'MessageSquare' },
  ];
}
