import {describe,expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {AuthDialog} from './AuthDialog';

describe('authentication API contract',()=>{
  it('accepts the full password length supported by the backend',()=>{
    const html=renderToStaticMarkup(<AuthDialog locale="en" open onClose={()=>{}} onSuccess={()=>{}}/>);
    expect(html).toMatch(/type="password"[^>]*maxLength="1024"/i);
  });
});
