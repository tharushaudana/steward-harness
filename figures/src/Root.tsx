import React from 'react';
import {Composition, Still} from 'remotion';
import {Architecture, ARCH_H, ARCH_W} from './Architecture';
import {Dadr, DADR_H, DADR_W} from './Dadr';
import {KnowledgeRadial, KnowledgeStrikes, STRIKE_FRAMES} from './KnowledgeRadial';
import {LogoBanner, LogoIcon} from './Logo';
import type {Props} from './theme';

const light: Props = {theme: 'light'};

export const RemotionRoot: React.FC = () => (
  <>
    <Still id="LogoBanner" component={LogoBanner} width={1600} height={420} defaultProps={light} />
    <Still id="LogoIcon" component={LogoIcon} width={512} height={512} defaultProps={light} />
    <Still id="Architecture" component={Architecture} width={ARCH_W} height={ARCH_H} defaultProps={light} />
    <Still id="Dadr" component={Dadr} width={DADR_W} height={DADR_H} defaultProps={light} />
    <Still id="KnowledgeRadial" component={KnowledgeRadial} width={1600} height={1600} defaultProps={light} />
    <Composition id="KnowledgeStrikes" component={KnowledgeStrikes} width={760} height={760} fps={30}
      durationInFrames={STRIKE_FRAMES} defaultProps={{theme: 'dark'} as Props} />
  </>
);
