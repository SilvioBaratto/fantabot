import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';

import { IconName } from '../../icons';
import { ThemeMode, ThemeService } from '../../services/theme';

interface ThemeOption {
  mode: ThemeMode;
  label: string;
  icon: IconName;
}

@Component({
  selector: 'app-settings',
  imports: [LucideAngularModule],
  templateUrl: './settings.html',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'block p-6 md:p-8' },
})
export class SettingsComponent {
  private readonly themeService = inject(ThemeService);

  readonly theme = this.themeService.theme;

  options: ThemeOption[] = [
    { mode: 'system', label: 'System', icon: 'Monitor' },
    { mode: 'light', label: 'Light', icon: 'Sun' },
    { mode: 'dark', label: 'Dark', icon: 'Moon' },
  ];

  select(mode: ThemeMode) {
    this.themeService.setTheme(mode);
  }
}
